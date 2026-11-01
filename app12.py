# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-11-01（第 12 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 12 天新增与修改的源代码，不含第 1~11 天已有代码
#           本次迭代主题：设备巡检计划与执行管理模块 + 设备巡检页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5001/md/overview）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 12 天新增业务模块 1：mdm_patrol.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-11-01（第 12 天 / 共 15 天）
# 模块功能：设备巡检计划与执行管理模块。实现巡检计划编制（巡检类型、巡检周期、
#           覆盖范围、生效区间与负责人）、按周期自动生成巡检任务与巡检明细、
#           逐项登记检查结果、异常项目一键转维修工单、任务完成归档与超期扫描
#           全过程管理；任务完成后自动回算正常项、异常项与巡检得分，超期未完
#           成的任务自动标记并累计；输出巡检类型分布、任务状态分布、项目结论
#           分布、科室巡检完成率对比与近 6 个月巡检趋势五类统计图表，形成
#           「计划编制 → 任务生成 → 逐项检查 → 异常转单 → 归档评价」的巡检闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from mdm_dal import get_connection, write_ops_journal

# ------------------------------------------------------------------
# 巡检类型字典：区分日常巡检、安全巡检、预防性维护与专项巡检四类
# ------------------------------------------------------------------
PATROL_TYPE_DICT = {
    "ROUTINE": "日常巡检",
    "SAFETY": "安全巡检",
    "PREVENT": "预防性维护",
    "SPECIAL": "专项巡检",
}

# 巡检周期字典：按日、周、月、季四种节奏自动生成巡检任务
PATROL_CYCLE_DICT = {
    "DAY": "每日",
    "WEEK": "每周",
    "MONTH": "每月",
    "QUARTER": "每季",
}

# 各周期对应的间隔天数，用于推算下一次巡检日期
CYCLE_DAYS_MAP = {
    "DAY": 1,
    "WEEK": 7,
    "MONTH": 30,
    "QUARTER": 90,
}

# 计划状态字典
PATROL_PLAN_STATE_DICT = {
    "RUN": "执行中",
    "PAUSE": "已暂停",
    "STOP": "已终止",
}

# 任务状态字典：随执行、归档逐级流转
PATROL_TASK_STATE_DICT = {
    "WAIT": "待执行",
    "DOING": "执行中",
    "DONE": "已完成",
    "VOID": "已作废",
}

# 巡检项目结论字典
PATROL_ITEM_DICT = {
    "NORMAL": "正常",
    "ABNORMAL": "异常",
    "NOTDO": "未检",
}

# 未完成任务状态集合：超期扫描只处理这些状态的任务
PATROL_OPEN_STATE_LIST = ("WAIT", "DOING")

# 默认巡检项目模板：生成巡检明细时逐条落库，保证全院巡检口径统一
# （项目名称，检查方法，合格标准）
DEFAULT_PATROL_ITEM_LIST = (
    ("外观与标识检查", "目视检查外壳、铭牌与资产标签", "外壳无破损，标识清晰可辨"),
    ("通电运行状态", "通电试运行并观察启动自检过程", "启动正常，自检无报错"),
    ("噪声与振动", "运行中听诊噪声并观察机体振动", "无异响，振动在允许范围"),
    ("关键参数核对", "比对设备显示值与标准器读数", "示值偏差在技术允许范围"),
    ("电气安全接地", "检查电源线、接地线与插头完整性", "线缆完好，接地可靠"),
    ("配件与耗材", "清点随机配件与在用耗材存量", "配件齐全，耗材充足"),
)

# 巡检负责人候选，供计划编制选择与演示数据灌入
PATROL_OWNER_LIST = ("医工科-周工", "设备科-孙工", "维修班-钱班长")

# 分页尺寸：计划与任务各自独立分页
PLAN_PAGE_SIZE = 10
TASK_PAGE_SIZE = 8

# 演示计划清单，用于首日灌入演示数据
# （计划名称，巡检类型，巡检周期，范围模式，覆盖设备台数，计划状态）
DEMO_PATROL_LIST = (
    ("全院急救生命支持类设备日常巡检", "ROUTINE", "WEEK", "ALL", 5, "RUN"),
    ("医学装备电气安全与接地专项巡检", "SAFETY", "QUARTER", "ALL", 3, "RUN"),
    ("重点科室设备预防性维护巡检", "PREVENT", "MONTH", "DEPT", 3, "RUN"),
    ("新装机设备跟踪专项巡检", "SPECIAL", "DAY", "DEPT", 2, "PAUSE"),
)

# 演示任务生成日期（相对今天的天数偏移，负数表示过去）
DEMO_TASK_DATES = (-72, -42, -15, -4)


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


def parse_datetime_text(text):
    """把 YYYY-MM-DD HH:MM:SS 文本解析为日期时间对象，失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text[:19], pattern)
        except (TypeError, ValueError):
            continue
    return None


def now_text():
    """返回当前时间的 YYYY-MM-DD HH:MM:SS 文本。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def round1(value):
    """把数值保留一位小数，空值按 0 处理。"""
    try:
        return round(float(value or 0), 1)
    except (TypeError, ValueError):
        return 0.0


def next_seq_no(table_name):
    """读取业务表的自增主键最大值，用于生成不重复的流水编号。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(%s_id),0) AS m FROM %s" % (
                table_name.replace("md_patrol_", ""), table_name)).fetchone()
    finally:
        conn.close()
    return int(row["m"] or 0) + 1


def build_plan_code(seq_no):
    """生成巡检计划编号，形如 PP-0000007。"""
    return "PP-%07d" % seq_no


def build_task_code(seq_no):
    """生成巡检任务编号，形如 PT-0000043。"""
    return "PT-%07d" % seq_no


def build_item_code(seq_no):
    """生成巡检明细编号，形如 PI-0000258。"""
    return "PI-%07d" % seq_no


def calc_patrol_score(normal_amount, abnormal_amount):
    """按已检项目推算巡检得分：正常项占比百分制，未检项不计入分母。"""
    checked = int(normal_amount or 0) + int(abnormal_amount or 0)
    if checked <= 0:
        return 0.0
    return round1(normal_amount * 100.0 / checked)


def judge_next_cycle(cycle_code, base_date):
    """按巡检周期推算下一次巡检日期。"""
    days = CYCLE_DAYS_MAP.get(cycle_code, 30)
    return date_to_text(base_date + datetime.timedelta(days=days))


# ------------------------------------------------------------------
# 查询辅助
# ------------------------------------------------------------------
def fetch_device_options(limit=80, dept_name=""):
    """读取台账设备候选，供巡检计划覆盖设备与任务生成使用。"""
    dept_name = (dept_name or "").strip()
    sql = ("SELECT asset_code, asset_name, dept_name FROM md_asset_ledger ")
    args = []
    if dept_name:
        sql += "WHERE dept_name=? "
        args.append(dept_name)
    sql += "ORDER BY asset_id ASC LIMIT ?"
    args.append(limit)
    conn = get_connection()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    return [dict(item) for item in rows]


def fetch_plan_row(plan_code):
    """按计划编号读取一条巡检计划，不存在返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_patrol_plan WHERE plan_code=?",
            ((plan_code or "").strip(),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def fetch_task_row(task_code):
    """按任务编号读取一条巡检任务，不存在返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_patrol_task WHERE task_code=?",
            ((task_code or "").strip(),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def fetch_task_items(task_code):
    """读取某条巡检任务下的全部巡检明细。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM md_patrol_item WHERE task_code=? "
            "ORDER BY item_id ASC", ((task_code or "").strip(),)).fetchall()
    finally:
        conn.close()
    return [dict(item) for item in rows]


# ------------------------------------------------------------------
# 巡检计划编制
# ------------------------------------------------------------------
def save_patrol_plan(plan_code, plan_name, patrol_type="ROUTINE",
                     cycle_code="MONTH", scope_dept="", start_date="",
                     end_date="", owner_name="", plan_state="RUN",
                     remark_text="", operator="admin"):
    """新建或编辑一条巡检计划，并自动回算覆盖设备台数与单次项目数。

    计划编号留空表示新建；覆盖范围留空表示覆盖全院设备，填写科室时只
    统计该科室设备；单次项目数取默认巡检项目模板的条数。

    :return: 含 success、message 与 plan_code 的执行结果字典
    """
    plan_name = (plan_name or "").strip()
    if not plan_name:
        return {"success": False, "message": "请填写巡检计划名称"}
    if patrol_type not in PATROL_TYPE_DICT:
        return {"success": False, "message": "巡检类型 %s 不合法" % patrol_type}
    if cycle_code not in PATROL_CYCLE_DICT:
        return {"success": False, "message": "巡检周期 %s 不合法" % cycle_code}
    if plan_state not in PATROL_PLAN_STATE_DICT:
        return {"success": False, "message": "计划状态 %s 不合法" % plan_state}

    device_list = fetch_device_options(limit=200, dept_name=scope_dept)
    device_amount = len(device_list)
    conn = get_connection()
    try:
        exist = None
        if (plan_code or "").strip():
            exist = conn.execute(
                "SELECT plan_code FROM md_patrol_plan WHERE plan_code=?",
                (plan_code.strip(),)).fetchone()
        if exist is None:
            code = build_plan_code(next_seq_no("md_patrol_plan"))
            conn.execute(
                "INSERT INTO md_patrol_plan (plan_code, plan_name, patrol_type, "
                "cycle_code, scope_dept, device_amount, item_amount, start_date, "
                "end_date, owner_name, plan_state, remark_text, operator_name) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, plan_name, patrol_type, cycle_code, (scope_dept or "").strip(),
                 device_amount, len(DEFAULT_PATROL_ITEM_LIST),
                 (start_date or "").strip(), (end_date or "").strip(),
                 (owner_name or "").strip(), plan_state,
                 (remark_text or "").strip(), (operator or "admin").strip()))
            action = "计划编制"
        else:
            code = plan_code.strip()
            conn.execute(
                "UPDATE md_patrol_plan SET plan_name=?, patrol_type=?, "
                "cycle_code=?, scope_dept=?, device_amount=?, item_amount=?, "
                "start_date=?, end_date=?, owner_name=?, plan_state=?, "
                "remark_text=?, operator_name=? WHERE plan_code=?",
                (plan_name, patrol_type, cycle_code, (scope_dept or "").strip(),
                 device_amount, len(DEFAULT_PATROL_ITEM_LIST),
                 (start_date or "").strip(), (end_date or "").strip(),
                 (owner_name or "").strip(), plan_state,
                 (remark_text or "").strip(), (operator or "admin").strip(), code))
            action = "计划编辑"
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("设备巡检", action, code,
                      "%s 巡检计划「%s」，覆盖 %d 台设备" % (
                          action, plan_name, device_amount), operator)
    return {"success": True, "message": "巡检计划 %s 已保存" % code,
            "plan_code": code}


# ------------------------------------------------------------------
# 巡检任务生成
# ------------------------------------------------------------------
def generate_plan_tasks(plan_code, plan_date="", device_limit=0,
                        operator="admin"):
    """按巡检计划生成一期巡检任务，并为每台设备展开全套巡检明细。

    同一计划、同一设备、同一巡检日期的任务不会重复生成；巡检明细取默认
    巡检项目模板，生成时结论统一为未检。

    :return: 含 success、message、amount 与 task_codes 的执行结果字典
    """
    plan = fetch_plan_row(plan_code)
    if plan is None:
        return {"success": False, "message": "巡检计划不存在"}
    if plan["plan_state"] != "RUN":
        return {"success": False, "message": "仅执行中的计划可以生成巡检任务"}
    day = parse_date_text(plan_date) or today_date()
    plan_day_text = date_to_text(day)
    device_list = fetch_device_options(limit=200, dept_name=plan["scope_dept"])
    if device_limit and device_limit > 0:
        device_list = device_list[:device_limit]
    if not device_list:
        return {"success": False, "message": "该计划覆盖范围内没有可巡检的设备"}

    conn = get_connection()
    try:
        exist_rows = conn.execute(
            "SELECT device_code FROM md_patrol_task WHERE plan_code=? "
            "AND plan_date=?", (plan["plan_code"], plan_day_text)).fetchall()
        exist_codes = {row["device_code"] for row in exist_rows}
        task_seq = next_seq_no("md_patrol_task")
        item_seq = next_seq_no("md_patrol_item")
        task_codes = []
        amount = 0
        for device in device_list:
            if device["asset_code"] in exist_codes:
                continue
            task_code = build_task_code(task_seq)
            conn.execute(
                "INSERT INTO md_patrol_task (task_code, plan_code, plan_name, "
                "device_code, device_name, dept_name, patrol_type, plan_date, "
                "executor_name, task_state, item_total, operator_name) "
                "VALUES (?,?,?,?,?,?,?,?,?,'WAIT',?,?)",
                (task_code, plan["plan_code"], plan["plan_name"],
                 device["asset_code"], device["asset_name"],
                 device["dept_name"] or "", plan["patrol_type"],
                 plan_day_text, (plan["owner_name"] or "").strip(),
                 len(DEFAULT_PATROL_ITEM_LIST), (operator or "admin").strip()))
            for name, method, standard in DEFAULT_PATROL_ITEM_LIST:
                conn.execute(
                    "INSERT INTO md_patrol_item (item_code, task_code, "
                    "plan_code, device_code, item_name, check_method, "
                    "std_value, item_result, checker_name) "
                    "VALUES (?,?,?,?,?,?,?,'NOTDO',?)",
                    (build_item_code(item_seq), task_code,
                     plan["plan_code"], device["asset_code"], name, method,
                     standard, (plan["owner_name"] or "").strip()))
                item_seq += 1
            task_seq += 1
            task_codes.append(task_code)
            amount += 1
        conn.commit()
    finally:
        conn.close()
    if not amount:
        return {"success": False, "message": "该日期的巡检任务已生成，无需重复生成"}
    write_ops_journal("设备巡检", "任务生成", plan["plan_code"],
                      "生成 %s 的 %d 条巡检任务" % (plan_day_text, amount),
                      operator)
    return {"success": True,
            "message": "已生成 %d 条巡检任务" % amount,
            "amount": amount, "task_codes": task_codes}


def scan_overdue_patrol(operator="admin"):
    """扫描超期未完成的巡检任务并标记超期，返回本次新标记条数。"""
    today = date_to_text(today_date())
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT task_code FROM md_patrol_task WHERE task_state IN "
            "('WAIT','DOING') AND overdue_flag=0 AND plan_date<>'' "
            "AND plan_date<?", (today,)).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE md_patrol_task SET overdue_flag=1 WHERE task_code=?",
                (row["task_code"],))
        conn.commit()
    finally:
        conn.close()
    return len(rows)


# ------------------------------------------------------------------
# 巡检结果登记与归档
# ------------------------------------------------------------------
def save_patrol_item_result(item_code, check_value="", item_result="NORMAL",
                            abnormal_desc="", checker_name="", operator="admin"):
    """登记单条巡检明细的检查结果，并回算所属任务的正常项与异常项。

    结论为异常时必须填写异常描述；登记后自动重算任务正常项、异常项与
    巡检得分，全部项目登记完成后任务自动推进为执行中。

    :return: 含 success 与 message 的执行结果字典
    """
    if item_result not in PATROL_ITEM_DICT:
        return {"success": False, "message": "检查结论 %s 不合法" % item_result}
    if item_result == "ABNORMAL" and not (abnormal_desc or "").strip():
        return {"success": False, "message": "结论为异常时请填写异常描述"}
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_patrol_item WHERE item_code=?",
            ((item_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "巡检明细不存在"}
        if row["handle_flag"]:
            return {"success": False, "message": "该异常项已转维修工单，不可再修改"}
        conn.execute(
            "UPDATE md_patrol_item SET check_value=?, item_result=?, "
            "abnormal_desc=?, checker_name=? WHERE item_code=?",
            ((check_value or "").strip(), item_result,
             (abnormal_desc or "").strip(), (checker_name or "").strip(),
             row["item_code"]))
        stat = conn.execute(
            "SELECT SUM(CASE WHEN item_result='NORMAL' THEN 1 ELSE 0 END) AS ok, "
            "SUM(CASE WHEN item_result='ABNORMAL' THEN 1 ELSE 0 END) AS bad, "
            "SUM(CASE WHEN item_result='NOTDO' THEN 1 ELSE 0 END) AS todo "
            "FROM md_patrol_item WHERE task_code=?", (row["task_code"],)).fetchone()
        task = conn.execute(
            "SELECT * FROM md_patrol_task WHERE task_code=?",
            (row["task_code"],)).fetchone()
        if task is not None and task["task_state"] == "WAIT":
            conn.execute(
                "UPDATE md_patrol_task SET task_state='DOING' WHERE task_code=?",
                (row["task_code"],))
        if task is not None:
            conn.execute(
                "UPDATE md_patrol_task SET normal_amount=?, abnormal_amount=?, "
                "patrol_score=? WHERE task_code=?",
                (int(stat["ok"] or 0), int(stat["bad"] or 0),
                 calc_patrol_score(int(stat["ok"] or 0), int(stat["bad"] or 0)),
                 row["task_code"]))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("设备巡检", "结果登记", row["item_code"],
                      "登记巡检项「%s」结论 %s" % (
                          row["item_name"], PATROL_ITEM_DICT[item_result]),
                      operator)
    return {"success": True, "message": "巡检明细 %s 已登记" % row["item_code"]}


def finish_patrol_task(task_code, executor_name="", remark_text="",
                       finish_date="", operator="admin"):
    """归档一条巡检任务：写入完成日期与巡检小结并回算最终巡检得分。"""
    task = fetch_task_row(task_code)
    if task is None:
        return {"success": False, "message": "巡检任务不存在"}
    if task["task_state"] == "DONE":
        return {"success": False, "message": "该巡检任务已归档"}
    if task["task_state"] == "VOID":
        return {"success": False, "message": "已作废的巡检任务不可归档"}
    conn = get_connection()
    try:
        stat = conn.execute(
            "SELECT SUM(CASE WHEN item_result='NORMAL' THEN 1 ELSE 0 END) AS ok, "
            "SUM(CASE WHEN item_result='ABNORMAL' THEN 1 ELSE 0 END) AS bad, "
            "COUNT(1) AS total FROM md_patrol_item WHERE task_code=?",
            (task["task_code"],)).fetchone()
        normal_amount = int(stat["ok"] or 0)
        abnormal_amount = int(stat["bad"] or 0)
        conn.execute(
            "UPDATE md_patrol_task SET task_state='DONE', finish_date=?, "
            "executor_name=?, remark_text=?, normal_amount=?, "
            "abnormal_amount=?, patrol_score=? WHERE task_code=?",
            ((parse_date_text(finish_date) or today_date()).strftime("%Y-%m-%d"),
             (executor_name or "").strip() or task["executor_name"],
             (remark_text or "").strip(), normal_amount, abnormal_amount,
             calc_patrol_score(normal_amount, abnormal_amount),
             task["task_code"]))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("设备巡检", "任务归档", task["task_code"],
                      "归档巡检任务，得分 %s 分" % calc_patrol_score(
                          normal_amount, abnormal_amount), operator)
    return {"success": True, "message": "巡检任务 %s 已归档" % task["task_code"]}


def cancel_patrol_task(task_code, reason_text="", operator="admin"):
    """作废一条巡检任务：已归档的任务不可作废，作废后不再计入完成率。"""
    task = fetch_task_row(task_code)
    if task is None:
        return {"success": False, "message": "巡检任务不存在"}
    if task["task_state"] == "DONE":
        return {"success": False, "message": "已归档的巡检任务不可作废"}
    if task["task_state"] == "VOID":
        return {"success": False, "message": "该巡检任务已作废"}
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_patrol_task SET task_state='VOID', remark_text=? "
            "WHERE task_code=?", ((reason_text or "").strip(), task["task_code"]))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("设备巡检", "任务作废", task["task_code"],
                      "作废巡检任务：%s" % ((reason_text or "").strip() or "未填写原因"),
                      operator)
    return {"success": True, "message": "巡检任务 %s 已作废" % task["task_code"]}


def create_repair_from_abnormal(item_code, fault_level="MID", fault_type="MECH",
                                report_person="", operator="admin"):
    """把异常巡检项一键转为维修工单，并回写处置标记与工单编号。

    仅结论为异常且未处置过的巡检项允许转单；转单成功后巡检项标记已处置，
    形成「巡检发现异常 → 自动生成报修 → 维修闭环」的联动。

    :return: 含 success、message 与 order_code 的执行结果字典
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_patrol_item WHERE item_code=?",
            ((item_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "巡检明细不存在"}
        if row["item_result"] != "ABNORMAL":
            return {"success": False, "message": "仅异常巡检项可以转维修工单"}
        if row["handle_flag"]:
            return {"success": False, "message": "该异常项已转维修工单"}
        device_row = conn.execute(
            "SELECT asset_name, dept_name FROM md_asset_ledger "
            "WHERE asset_code=?", (row["device_code"],)).fetchone()
    finally:
        conn.close()

    from mdm_repair import create_repair_order

    device_name = device_row["asset_name"] if device_row else ""
    dept_name = device_row["dept_name"] if device_row else ""
    fault_desc = "巡检发现「%s」异常：%s" % (
        row["item_name"], (row["abnormal_desc"] or "").strip() or "详见巡检记录")
    outcome = create_repair_order(
        row["device_code"], fault_type, fault_level, fault_desc,
        (report_person or "").strip() or (operator or "巡检员"), "",
        now_text(), dept_name, device_name,
        "由巡检明细 %s 自动转单" % row["item_code"], operator)
    if not outcome.get("success"):
        return outcome
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_patrol_item SET handle_flag=1, repair_code=? "
            "WHERE item_code=?", (outcome["order_code"], row["item_code"]))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("设备巡检", "异常转单", row["item_code"],
                      "异常项转为维修工单 %s" % outcome["order_code"], operator)
    return {"success": True,
            "message": "已生成维修工单 %s" % outcome["order_code"],
            "order_code": outcome["order_code"]}


# ------------------------------------------------------------------
# 分页查询
# ------------------------------------------------------------------
def fetch_patrol_plans(keyword="", patrol_type="", plan_state="", page=1):
    """按条件分页查询巡检计划，返回分页结果与记录列表。"""
    sql = ("SELECT * FROM md_patrol_plan WHERE 1=1 ")
    args = []
    keyword = (keyword or "").strip()
    if keyword:
        sql += ("AND (plan_code LIKE ? OR plan_name LIKE ? OR scope_dept LIKE ? "
                "OR owner_name LIKE ?) ")
        like = "%%%s%%" % keyword
        args.extend([like, like, like, like])
    if patrol_type:
        sql += "AND patrol_type=? "
        args.append(patrol_type)
    if plan_state:
        sql += "AND plan_state=? "
        args.append(plan_state)
    conn = get_connection()
    try:
        total = conn.execute(
            sql.replace("SELECT *", "SELECT COUNT(1) AS c", 1), args).fetchone()["c"]
        page_amount = max(1, (total + PLAN_PAGE_SIZE - 1) // PLAN_PAGE_SIZE)
        try:
            page_no = max(1, min(int(page), page_amount))
        except (TypeError, ValueError):
            page_no = 1
        rows = conn.execute(
            sql + "ORDER BY plan_id DESC LIMIT ? OFFSET ?",
            args + [PLAN_PAGE_SIZE, (page_no - 1) * PLAN_PAGE_SIZE]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["type_label"] = PATROL_TYPE_DICT.get(item["patrol_type"], item["patrol_type"])
        record["cycle_label"] = PATROL_CYCLE_DICT.get(item["cycle_code"], item["cycle_code"])
        record["state_label"] = PATROL_PLAN_STATE_DICT.get(item["plan_state"], item["plan_state"])
        record["scope_text"] = (item["scope_dept"] or "").strip() or "全院设备"
        records.append(record)
    return {"records": records, "total": total, "page": page_no,
            "page_amount": page_amount}


def fetch_patrol_tasks(plan_code="", task_state="", keyword="",
                       abnormal_only=False, page=1):
    """按条件分页查询巡检任务，返回分页结果与记录列表。"""
    sql = ("SELECT * FROM md_patrol_task WHERE 1=1 ")
    args = []
    if plan_code:
        sql += "AND plan_code=? "
        args.append(plan_code)
    if task_state:
        sql += "AND task_state=? "
        args.append(task_state)
    keyword = (keyword or "").strip()
    if keyword:
        sql += ("AND (task_code LIKE ? OR device_name LIKE ? OR dept_name LIKE ? "
                "OR executor_name LIKE ?) ")
        like = "%%%s%%" % keyword
        args.extend([like, like, like, like])
    if abnormal_only:
        sql += "AND abnormal_amount>0 "
    conn = get_connection()
    try:
        total = conn.execute(
            sql.replace("SELECT *", "SELECT COUNT(1) AS c", 1), args).fetchone()["c"]
        page_amount = max(1, (total + TASK_PAGE_SIZE - 1) // TASK_PAGE_SIZE)
        try:
            page_no = max(1, min(int(page), page_amount))
        except (TypeError, ValueError):
            page_no = 1
        rows = conn.execute(
            sql + "ORDER BY plan_date DESC, task_id DESC LIMIT ? OFFSET ?",
            args + [TASK_PAGE_SIZE, (page_no - 1) * TASK_PAGE_SIZE]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["type_label"] = PATROL_TYPE_DICT.get(item["patrol_type"], item["patrol_type"])
        record["state_label"] = PATROL_TASK_STATE_DICT.get(item["task_state"], item["task_state"])
        record["abnormal_label"] = ("%d 项异常" % item["abnormal_amount"]
                                    if item["abnormal_amount"] else "无异常")
        records.append(record)
    return {"records": records, "total": total, "page": page_no,
            "page_amount": page_amount}


def fetch_patrol_items(task_code="", item_result="", keyword="", limit=30):
    """按条件查询巡检明细，支持按任务、结论与关键字过滤。"""
    sql = ("SELECT * FROM md_patrol_item WHERE 1=1 ")
    args = []
    if task_code:
        sql += "AND task_code=? "
        args.append(task_code)
    if item_result:
        sql += "AND item_result=? "
        args.append(item_result)
    keyword = (keyword or "").strip()
    if keyword:
        sql += "AND (item_code LIKE ? OR item_name LIKE ? OR abnormal_desc LIKE ?) "
        like = "%%%s%%" % keyword
        args.extend([like, like, like])
    conn = get_connection()
    try:
        rows = conn.execute(
            sql + "ORDER BY item_id DESC LIMIT ?", args + [limit]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["result_label"] = PATROL_ITEM_DICT.get(item["item_result"], item["item_result"])
        record["handle_label"] = "已转工单" if item["handle_flag"] else "未处置"
        records.append(record)
    return records


# ------------------------------------------------------------------
# 统计与图表
# ------------------------------------------------------------------
def build_patrol_summary():
    """汇总巡检核心指标：计划量、任务量、完成率、异常项、超期量与平均得分。"""
    scan_overdue_patrol()
    conn = get_connection()
    try:
        plan_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_patrol_plan").fetchone()["c"]
        run_plan = conn.execute(
            "SELECT COUNT(1) AS c FROM md_patrol_plan WHERE plan_state='RUN'"
        ).fetchone()["c"]
        state_rows = conn.execute(
            "SELECT task_state, COUNT(1) AS c FROM md_patrol_task "
            "GROUP BY task_state").fetchall()
        task_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_patrol_task").fetchone()["c"]
        overdue = conn.execute(
            "SELECT COUNT(1) AS c FROM md_patrol_task WHERE overdue_flag=1"
        ).fetchone()["c"]
        abnormal_task = conn.execute(
            "SELECT COUNT(1) AS c FROM md_patrol_task WHERE abnormal_amount>0"
        ).fetchone()["c"]
        item_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_patrol_item").fetchone()["c"]
        abnormal_item = conn.execute(
            "SELECT COUNT(1) AS c FROM md_patrol_item WHERE item_result='ABNORMAL'"
        ).fetchone()["c"]
        handled_item = conn.execute(
            "SELECT COUNT(1) AS c FROM md_patrol_item WHERE handle_flag=1"
        ).fetchone()["c"]
        score_row = conn.execute(
            "SELECT COALESCE(AVG(patrol_score),0) AS a FROM md_patrol_task "
            "WHERE task_state='DONE'").fetchone()
    finally:
        conn.close()

    state_counter = {item["task_state"]: item["c"] for item in state_rows}
    done_amount = state_counter.get("DONE", 0)
    finish_rate = round1(done_amount * 100.0 / task_total) if task_total else 0.0
    normal_rate = round1((item_total - abnormal_item) * 100.0 / item_total) if item_total else 0.0
    return {
        "plan_total": plan_total,
        "run_plan": run_plan,
        "task_total": task_total,
        "done_amount": done_amount,
        "wait_amount": state_counter.get("WAIT", 0),
        "doing_amount": state_counter.get("DOING", 0),
        "overdue_amount": overdue,
        "abnormal_task": abnormal_task,
        "item_total": item_total,
        "abnormal_item": abnormal_item,
        "handled_item": handled_item,
        "finish_rate": finish_rate,
        "normal_rate": normal_rate,
        "avg_score": round1(score_row["a"]),
    }


def build_patrol_charts():
    """构建五类图表数据：巡检类型分布、任务状态分布、项目结论分布、科室
    巡检完成率对比与近 6 个月巡检任务趋势。"""
    conn = get_connection()
    try:
        task_rows = conn.execute(
            "SELECT task_code, patrol_type, task_state, dept_name, plan_date, "
            "finish_date, abnormal_amount, patrol_score FROM md_patrol_task").fetchall()
        result_rows = conn.execute(
            "SELECT item_result, COUNT(1) AS c FROM md_patrol_item "
            "GROUP BY item_result").fetchall()
    finally:
        conn.close()

    type_counter = {}
    for item in task_rows:
        type_counter[item["patrol_type"]] = type_counter.get(item["patrol_type"], 0) + 1
    type_distribution = [
        {"name": PATROL_TYPE_DICT.get(code, code), "value": amount}
        for code, amount in type_counter.items()
    ]

    state_counter = {}
    for item in task_rows:
        state_counter[item["task_state"]] = state_counter.get(item["task_state"], 0) + 1
    task_distribution = [
        {"name": PATROL_TASK_STATE_DICT.get(code, code),
         "value": state_counter.get(code, 0)}
        for code in ("WAIT", "DOING", "DONE", "VOID")
    ]

    result_counter = {item["item_result"]: item["c"] for item in result_rows}
    item_distribution = [
        {"name": PATROL_ITEM_DICT.get(code, code), "value": result_counter.get(code, 0)}
        for code in ("NORMAL", "ABNORMAL", "NOTDO")
    ]

    dept_total = {}
    dept_done = {}
    for item in task_rows:
        dept = (item["dept_name"] or "").strip() or "未指定科室"
        dept_total[dept] = dept_total.get(dept, 0) + 1
        if item["task_state"] == "DONE":
            dept_done[dept] = dept_done.get(dept, 0) + 1
    dept_rate = []
    for dept, total in dept_total.items():
        dept_rate.append({
            "name": dept,
            "value": round1(dept_done.get(dept, 0) * 100.0 / total) if total else 0.0,
            "total": total,
        })
    dept_rate.sort(key=lambda x: x["value"], reverse=True)

    month_keys = []
    cursor = today_date().replace(day=1)
    for _ in range(6):
        month_keys.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - datetime.timedelta(days=1)).replace(day=1)
    month_keys.reverse()
    plan_counter = {key: 0 for key in month_keys}
    finish_counter = {key: 0 for key in month_keys}
    for item in task_rows:
        plan_key = parse_date_text(item["plan_date"])
        if plan_key is not None and plan_key.strftime("%Y-%m") in plan_counter:
            plan_counter[plan_key.strftime("%Y-%m")] += 1
        end_key = parse_date_text(item["finish_date"])
        if end_key is not None and item["task_state"] == "DONE":
            month_key = end_key.strftime("%Y-%m")
            if month_key in finish_counter:
                finish_counter[month_key] += 1

    return {
        "type_distribution": type_distribution,
        "task_distribution": task_distribution,
        "item_distribution": item_distribution,
        "dept_rate": dept_rate[:8],
        "month_trend": {
            "months": month_keys,
            "plan_values": [plan_counter[key] for key in month_keys],
            "finish_values": [finish_counter[key] for key in month_keys],
        },
    }


def fetch_patrol_options():
    """巡检页面下拉选项：各类字典项、设备候选、科室候选与负责人候选。"""
    conn = get_connection()
    try:
        dept_rows = conn.execute(
            "SELECT DISTINCT dept_name FROM md_asset_ledger WHERE dept_name<>'' "
            "ORDER BY dept_name").fetchall()
        plan_rows = conn.execute(
            "SELECT plan_code, plan_name FROM md_patrol_plan "
            "ORDER BY plan_id DESC LIMIT 20").fetchall()
    finally:
        conn.close()
    return {
        "device_list": fetch_device_options(),
        "dept_list": [row["dept_name"] for row in dept_rows],
        "owner_list": list(PATROL_OWNER_LIST),
        "plan_list": [dict(row) for row in plan_rows],
        "type_options": [{"code": k, "label": v} for k, v in PATROL_TYPE_DICT.items()],
        "cycle_options": [{"code": k, "label": v} for k, v in PATROL_CYCLE_DICT.items()],
        "state_options": [{"code": k, "label": v}
                          for k, v in PATROL_PLAN_STATE_DICT.items()],
        "task_options": [{"code": k, "label": v}
                         for k, v in PATROL_TASK_STATE_DICT.items()],
        "result_options": [{"code": k, "label": v} for k, v in PATROL_ITEM_DICT.items()],
    }


def build_patrol_payload(keyword="", patrol_type="", plan_state="",
                         task_plan="", task_state="", task_keyword="",
                         abnormal_only=False, item_task="", item_result="",
                         item_keyword="", plan_page=1, task_page=1):
    """组装设备巡检计划与执行管理页面所需的全部数据。"""
    plan_pager = fetch_patrol_plans(keyword, patrol_type, plan_state, plan_page)
    task_pager = fetch_patrol_tasks(task_plan, task_state, task_keyword,
                                    abnormal_only, task_page)
    return {
        "summary": build_patrol_summary(),
        "charts": build_patrol_charts(),
        "plans": plan_pager["records"],
        "plan_total": plan_pager["total"],
        "plan_page": plan_pager["page"],
        "plan_page_amount": plan_pager["page_amount"],
        "tasks": task_pager["records"],
        "task_total": task_pager["total"],
        "task_page": task_pager["page"],
        "task_page_amount": task_pager["page_amount"],
        "items": fetch_patrol_items(item_task, item_result, item_keyword),
        "options": fetch_patrol_options(),
        "result": "OK",
    }


def build_patrol_brief():
    """设备巡检看板摘要，供运维总览看板嵌入。"""
    summary = build_patrol_summary()
    return {
        "plan_total": summary["plan_total"],
        "task_total": summary["task_total"],
        "done_amount": summary["done_amount"],
        "overdue_amount": summary["overdue_amount"],
        "abnormal_item": summary["abnormal_item"],
        "finish_rate": summary["finish_rate"],
        "normal_rate": summary["normal_rate"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_patrol():
    """灌入设备巡检演示数据：计划编制、任务生成、逐项登记、异常转单与归档。

    :return: 生成的巡检任务条数
    """
    conn = get_connection()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM md_patrol_plan").fetchone()["c"]
        if already:
            return 0
        dept_rows = conn.execute(
            "SELECT DISTINCT dept_name FROM md_asset_ledger WHERE dept_name<>'' "
            "ORDER BY dept_name").fetchall()
    finally:
        conn.close()
    dept_names = [row["dept_name"] for row in dept_rows]
    today = today_date()

    amount = 0
    for index, (plan_name, patrol_type, cycle_code, scope_mode,
                device_limit, plan_state) in enumerate(DEMO_PATROL_LIST):
        scope_dept = ""
        if scope_mode == "DEPT" and len(dept_names) > 1:
            scope_dept = dept_names[1]
        outcome = save_patrol_plan(
            "", plan_name, patrol_type, cycle_code, scope_dept,
            date_to_text(today - datetime.timedelta(days=180)),
            date_to_text(today + datetime.timedelta(days=180)),
            PATROL_OWNER_LIST[index % len(PATROL_OWNER_LIST)],
            plan_state, "演示用巡检计划，覆盖周期化巡检场景", "医工科-周工")
        if not outcome.get("success"):
            continue
        plan_code = outcome["plan_code"]
        # 暂停状态的计划只生成一期历史任务，执行中的计划生成四期
        date_offsets = DEMO_TASK_DATES if plan_state == "RUN" else (-72,)
        for offset in date_offsets:
            plan_day = date_to_text(today + datetime.timedelta(days=offset))
            gen = generate_plan_tasks(plan_code, plan_day, device_limit,
                                      "医工科-周工")
            amount += int(gen.get("amount") or 0)
            if not gen.get("success"):
                continue
            for step, task_code in enumerate(gen.get("task_codes") or []):
                items = fetch_task_items(task_code)
                # 最近一期保留待执行与执行中，用于演示超期与在检状态
                if offset == DEMO_TASK_DATES[-1] and plan_state == "RUN":
                    if step % 3 == 0:
                        for item in items[:3]:
                            save_patrol_item_result(
                                item["item_code"], "实测正常", "NORMAL",
                                "", "医工科-周工", "医工科-周工")
                    continue
                for item_index, item in enumerate(items):
                    # 每第 4 条任务留一项异常，用于演示异常转单与处置
                    make_abnormal = (step % 4 == 1) and item_index == 2
                    if make_abnormal:
                        save_patrol_item_result(
                            item["item_code"], "超出允许偏差 12%", "ABNORMAL",
                            "巡检实测值超出合格标准，需安排维修处理",
                            "医工科-周工", "医工科-周工")
                    else:
                        save_patrol_item_result(
                            item["item_code"], "符合标准", "NORMAL",
                            "", "医工科-周工", "医工科-周工")
                # 倒数第二期故意留下部分未归档任务，用于演示执行中状态与科室差异
                if offset == DEMO_TASK_DATES[-2] and step % 3 == 1:
                    continue
                finish_patrol_task(
                    task_code, PATROL_OWNER_LIST[index % len(PATROL_OWNER_LIST)],
                    "本轮巡检已完成，设备运行状况良好"
                    if step % 4 != 1 else "本轮巡检发现异常项，已按流程处置",
                    plan_day, "医工科-周工")

    # 作废一条待执行任务，用于演示任务作废与状态完整取值
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT task_code FROM md_patrol_task WHERE task_state='WAIT' "
            "ORDER BY task_id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    if row is not None:
        cancel_patrol_task(row["task_code"], "该设备已报废，本轮巡检任务作废",
                           "医工科-周工")

    # 抽取前 3 条未处置异常项转维修工单，形成巡检与维修的联动留痕
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT item_code FROM md_patrol_item WHERE item_result='ABNORMAL' "
            "AND handle_flag=0 ORDER BY item_id ASC LIMIT 3").fetchall()
    finally:
        conn.close()
    for row in rows:
        create_repair_from_abnormal(row["item_code"], "MID", "MECH",
                                    "巡检员-周工", "医工科-周工")
    return amount

# --------------------------------------------------------------------------
# 第 12 天数据表脚本增量：mdm_schema.sql（M，已内联为字符串常量 DAY12_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY12_SCHEMA_SQL = r"""
-- ------------------------------------------------------------------
-- 第 12 天新增：设备巡检计划与执行管理
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS md_patrol_plan (
    plan_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_code      TEXT    NOT NULL UNIQUE,           -- 巡检计划编号
    plan_name      TEXT    DEFAULT '',                -- 巡检计划名称
    patrol_type    TEXT    DEFAULT 'ROUTINE',         -- 巡检类型（见 PATROL_TYPE_DICT）
    cycle_code     TEXT    DEFAULT 'MONTH',           -- 巡检周期（见 PATROL_CYCLE_DICT）
    scope_dept     TEXT    DEFAULT '',                -- 巡检范围（科室，空表示全院）
    device_amount  INTEGER DEFAULT 0,                -- 覆盖设备台数
    item_amount    INTEGER DEFAULT 0,                -- 单次巡检项目数
    start_date     TEXT    DEFAULT '',                -- 计划生效日期
    end_date       TEXT    DEFAULT '',                -- 计划失效日期
    owner_name     TEXT    DEFAULT '',                -- 计划负责人
    plan_state     TEXT    DEFAULT 'RUN',             -- 计划状态（见 PATROL_PLAN_STATE_DICT）
    remark_text    TEXT    DEFAULT '',                -- 备注说明
    operator_name  TEXT    DEFAULT '',                -- 编制人
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS md_patrol_task (
    task_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_code       TEXT    NOT NULL UNIQUE,          -- 巡检任务编号
    plan_code       TEXT    DEFAULT '',               -- 所属计划编号
    plan_name       TEXT    DEFAULT '',               -- 所属计划名称快照
    device_code     TEXT    DEFAULT '',               -- 设备编号
    device_name     TEXT    DEFAULT '',               -- 设备名称快照
    dept_name       TEXT    DEFAULT '',               -- 所属科室快照
    patrol_type     TEXT    DEFAULT 'ROUTINE',        -- 巡检类型
    plan_date       TEXT    DEFAULT '',               -- 计划巡检日期
    finish_date     TEXT    DEFAULT '',               -- 实际完成日期
    executor_name   TEXT    DEFAULT '',               -- 巡检执行人
    task_state      TEXT    DEFAULT 'WAIT',           -- 任务状态（见 PATROL_TASK_STATE_DICT）
    item_total      INTEGER DEFAULT 0,                -- 巡检项目总数
    normal_amount   INTEGER DEFAULT 0,                -- 正常项目数
    abnormal_amount INTEGER DEFAULT 0,                -- 异常项目数
    patrol_score    REAL    DEFAULT 0,                -- 巡检得分（百分制）
    overdue_flag    INTEGER DEFAULT 0,                -- 是否超期未完成
    remark_text     TEXT    DEFAULT '',               -- 巡检小结
    operator_name   TEXT    DEFAULT '',               -- 生成人
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS md_patrol_item (
    item_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    item_code      TEXT    NOT NULL UNIQUE,           -- 巡检明细编号
    task_code      TEXT    DEFAULT '',               -- 所属任务编号
    plan_code      TEXT    DEFAULT '',               -- 所属计划编号
    device_code    TEXT    DEFAULT '',               -- 设备编号
    item_name      TEXT    DEFAULT '',               -- 巡检项目名称
    check_method   TEXT    DEFAULT '',               -- 检查方法
    std_value      TEXT    DEFAULT '',               -- 合格标准
    check_value    TEXT    DEFAULT '',               -- 实测值
    item_result    TEXT    DEFAULT 'NOTDO',          -- 项目结论（见 PATROL_ITEM_DICT）
    abnormal_desc  TEXT    DEFAULT '',               -- 异常描述
    handle_flag    INTEGER DEFAULT 0,                -- 是否已处置
    repair_code    TEXT    DEFAULT '',               -- 联动生成的维修工单号
    checker_name   TEXT    DEFAULT '',               -- 检查人
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_pplan_code       ON md_patrol_plan (plan_code);
CREATE INDEX IF NOT EXISTS idx_pplan_type       ON md_patrol_plan (patrol_type);
CREATE INDEX IF NOT EXISTS idx_pplan_state      ON md_patrol_plan (plan_state);
CREATE INDEX IF NOT EXISTS idx_ptask_code       ON md_patrol_task (task_code);
CREATE INDEX IF NOT EXISTS idx_ptask_plan       ON md_patrol_task (plan_code);
CREATE INDEX IF NOT EXISTS idx_ptask_device     ON md_patrol_task (device_code);
CREATE INDEX IF NOT EXISTS idx_ptask_state      ON md_patrol_task (task_state);
CREATE INDEX IF NOT EXISTS idx_ptask_date       ON md_patrol_task (plan_date);
CREATE INDEX IF NOT EXISTS idx_pitem_code       ON md_patrol_item (item_code);
CREATE INDEX IF NOT EXISTS idx_pitem_task       ON md_patrol_item (task_code);
CREATE INDEX IF NOT EXISTS idx_pitem_result     ON md_patrol_item (item_result);
"""


def apply_day12_schema(conn):
    """把第 12 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY12_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 12 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-11-01（第 12 天 / 共 15 天）
#           供应商合同管理、备件耗材库存管理、设备维修工单派工管理与设备巡检
#           计划执行管理十二套页面路由，并提供台账增删改查、闲置识别扫描、
#           状态切换、ROI 测算与年度收支档案维护、盘点任务生成与账实核对、

from mdm_patrol import (
    build_patrol_payload,
    build_patrol_brief,
    save_patrol_plan,
    generate_plan_tasks,
    save_patrol_item_result,
    finish_patrol_task,
    cancel_patrol_task,
    create_repair_from_abnormal,
    scan_overdue_patrol,
    fetch_patrol_plans,
    fetch_patrol_tasks,
    fetch_patrol_items,
    seed_demo_patrol,
    PATROL_TYPE_DICT,
    PATROL_CYCLE_DICT,
    PATROL_PLAN_STATE_DICT,
    PATROL_TASK_STATE_DICT,
    PATROL_ITEM_DICT,
)

# 【函数内插入】def api_overview():
#    # 第 12 天新增：总览叠加设备巡检摘要，供巡检概况卡渲染
#    payload["patrol_brief"] = build_patrol_brief()

@app.route("/md/patrol")
def panel_patrol():
    """设备巡检计划与执行管理页面：巡检概览、五类图表、计划任务与巡检明细。"""
    write_ops_journal("设备巡检", "查询", "PANEL-PATROL", "打开设备巡检计划与执行管理页面")
    return render_template(
        "panel_patrol.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/md/patrol/panel")
def api_patrol_panel():
    """巡检面板数据接口：概览指标、五类图表、计划、任务与巡检明细。"""
    return jsonify(build_patrol_payload(
        keyword=request.args.get("keyword", ""),
        patrol_type=request.args.get("patrol_type", ""),
        plan_state=request.args.get("plan_state", ""),
        task_plan=request.args.get("task_plan", ""),
        task_state=request.args.get("task_state", ""),
        task_keyword=request.args.get("task_keyword", ""),
        abnormal_only=request.args.get("abnormal_only", "") in ("1", "true", "True"),
        item_task=request.args.get("item_task", ""),
        item_result=request.args.get("item_result", ""),
        item_keyword=request.args.get("item_keyword", ""),
        plan_page=request.args.get("plan_page", 1),
        task_page=request.args.get("task_page", 1),
    ))


@app.route("/api/md/patrol/plan/save", methods=["POST"])
def api_patrol_plan_save():
    """巡检计划保存接口：新建或编辑计划并回算覆盖设备台数与项目数。"""
    body = request.get_json(silent=True) or {}
    result = save_patrol_plan(
        (body.get("plan_code") or "").strip(),
        body.get("plan_name") or "",
        body.get("patrol_type") or "ROUTINE",
        body.get("cycle_code") or "MONTH",
        body.get("scope_dept") or "",
        body.get("start_date") or "",
        body.get("end_date") or "",
        body.get("owner_name") or "",
        body.get("plan_state") or "RUN",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/patrol/task/generate", methods=["POST"])
def api_patrol_task_generate():
    """巡检任务生成接口：按计划与巡检日期批量生成任务并展开巡检明细。"""
    body = request.get_json(silent=True) or {}
    try:
        device_limit = int(body.get("device_limit") or 0)
    except (TypeError, ValueError):
        device_limit = 0
    result = generate_plan_tasks(
        (body.get("plan_code") or "").strip(),
        body.get("plan_date") or "",
        device_limit,
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/patrol/item/save", methods=["POST"])
def api_patrol_item_save():
    """巡检明细登记接口：登记单项检查结论并回算任务正常项与异常项。"""
    body = request.get_json(silent=True) or {}
    result = save_patrol_item_result(
        (body.get("item_code") or "").strip(),
        body.get("check_value") or "",
        body.get("item_result") or "NORMAL",
        body.get("abnormal_desc") or "",
        body.get("checker_name") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/patrol/task/finish", methods=["POST"])
def api_patrol_task_finish():
    """巡检任务归档接口：写入完成日期与巡检小结并回算最终巡检得分。"""
    body = request.get_json(silent=True) or {}
    result = finish_patrol_task(
        (body.get("task_code") or "").strip(),
        body.get("executor_name") or "",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/patrol/task/cancel", methods=["POST"])
def api_patrol_task_cancel():
    """巡检任务作废接口：作废未归档任务并登记作废原因。"""
    body = request.get_json(silent=True) or {}
    result = cancel_patrol_task(
        (body.get("task_code") or "").strip(),
        body.get("reason_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/patrol/item/torepair", methods=["POST"])
def api_patrol_item_torepair():
    """异常转单接口：把异常巡检项转为维修工单并回写处置标记。"""
    body = request.get_json(silent=True) or {}
    result = create_repair_from_abnormal(
        (body.get("item_code") or "").strip(),
        body.get("fault_level") or "MID",
        body.get("fault_type") or "MECH",
        body.get("report_person") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/patrol/scan", methods=["POST"])
def api_patrol_scan():
    """超期扫描接口：标记计划日期已过仍未完成的巡检任务。"""
    body = request.get_json(silent=True) or {}
    return jsonify({
        "result": "OK",
        "amount": scan_overdue_patrol((body.get("operator") or "admin").strip()),
    })


@app.route("/api/md/patrol/options")
def api_patrol_options():
    """巡检下拉选项接口：巡检类型、周期、计划状态、任务状态与项目结论字典。"""
    return jsonify({
        "result": "OK",
        "type_options": [{"code": k, "label": v} for k, v in PATROL_TYPE_DICT.items()],
        "cycle_options": [{"code": k, "label": v} for k, v in PATROL_CYCLE_DICT.items()],
        "state_options": [{"code": k, "label": v}
                          for k, v in PATROL_PLAN_STATE_DICT.items()],
        "task_options": [{"code": k, "label": v}
                         for k, v in PATROL_TASK_STATE_DICT.items()],
        "result_options": [{"code": k, "label": v} for k, v in PATROL_ITEM_DICT.items()],
    })



# 【函数内插入】def bootstrap_application():
#    seed_demo_patrol()

# 【函数内插入】if __name__ == "__main__":
#    print("设备巡检： http://127.0.0.1:%d/md/patrol" % PORT)

# --------------------------------------------------------------------------
# 第 12 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/mdm_patrol.js             新增 546 行
#   [修改] templates/layout_mdm.html        新增 1 行
#   [新增] templates/panel_patrol.html      新增 399 行
