# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-31（第 11 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 11 天新增与修改的源代码，不含第 1~10 天已有代码
#           本次迭代主题：设备维修工单与派工管理模块 + 维修工单页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5001/md/overview）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 11 天新增业务模块 1：mdm_repair.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-31（第 11 天 / 共 15 天）
# 模块功能：设备维修工单与派工管理模块。实现设备故障报修登记、按紧急度自动
#           推算完成时限、工程师派工与接单、完工反馈（故障原因、处理措施、
#           工时费用与领用备件）、科室验收评价与工单作废全过程管理；完工登记
#           领用备件时自动写入备件出库流水并扣减库存，超时扫描自动标记超时工
#           单并累计超时时长；输出故障类型分布、工单状态分布、紧急度分布、
#           维修人员工单量排行与近 6 个月报修完工趋势五类统计图表，形成
#           「报修受理 → 派工接单 → 完工反馈 → 验收评价」的维修闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from mdm_dal import get_connection, write_ops_journal

# ------------------------------------------------------------------
# 故障类型字典：区分机械、电气、软件与参数漂移四类常见故障
# ------------------------------------------------------------------
FAULT_TYPE_DICT = {
    "MECH": "机械故障",
    "ELEC": "电气故障",
    "SOFT": "软件故障",
    "PARA": "参数漂移",
    "OTHER": "其他故障",
}

# 紧急程度字典：特急最优先，时限最短
FAULT_LEVEL_DICT = {
    "LOW": "一般",
    "MID": "较急",
    "HIGH": "紧急",
    "URGENT": "特急",
}

# 各紧急度对应的要求完成时限（小时），用于自动推算要求完成时间
LEVEL_LIMIT_HOURS = {
    "LOW": 72,
    "MID": 48,
    "HIGH": 24,
    "URGENT": 8,
}

# 工单状态字典：随派工、完工、验收逐级流转
ORDER_STATE_DICT = {
    "WAIT": "待派工",
    "DISPATCH": "已派工",
    "DOING": "维修中",
    "DONE": "已完工",
    "VERIFY": "已验收",
    "CLOSE": "已关闭",
    "VOID": "已作废",
}

# 派工状态字典
DISPATCH_STATE_DICT = {
    "SENT": "已派发",
    "ACCEPT": "已接单",
    "REJECT": "已退回",
    "FINISH": "已完工",
}

# 维修结论字典
RESULT_CODE_DICT = {
    "FIXED": "修复正常",
    "PARTIAL": "部分恢复",
    "TRANSFER": "转外修",
    "SCRAP": "建议报废",
    "UNFIX": "未修复",
}

# 维修工程师候选，供派工弹窗选择与演示数据灌入
ENGINEER_LIST = (
    ("张工", "13800001101"),
    ("李工", "13800001102"),
    ("王工", "13800001103"),
    ("赵工", "13800001104"),
    ("陈工", "13800001105"),
)

# 未关闭状态集合：超时扫描只处理这些状态的工单
OPEN_STATE_LIST = ("WAIT", "DISPATCH", "DOING")

# 已结束状态集合：统计完工量时按这些状态计入
CLOSED_STATE_LIST = ("DONE", "VERIFY", "CLOSE")

# 分页尺寸：工单与派工各自独立分页
ORDER_PAGE_SIZE = 8
DISPATCH_PAGE_SIZE = 10

# 演示工单清单，用于首日灌入演示数据
# （设备序号，故障类型，紧急度，故障描述，报修人，报修电话，天数前，状态）
DEMO_REPAIR_LIST = (
    (0, "ELEC", "URGENT", "开机自检报错 E03，无法进入扫描界面", "设备科-刘护士", "13900002201", 148, "CLOSE"),
    (1, "MECH", "HIGH", "蠕动机构异响，输注速度偏差超 15%", "ICU-王护士", "13900002202", 121, "CLOSE"),
    (2, "SOFT", "MID", "工作站软件卡死，重启后仍无法采集图像", "放射科-孙技师", "13900002203", 95, "VERIFY"),
    (3, "PARA", "MID", "血氧饱和度读数偏低，需要重新标定", "急诊科-周护士", "13900002204", 66, "VERIFY"),
    (4, "ELEC", "HIGH", "电源模块指示灯熄灭，设备无输出", "手术室-吴护士", "13900002205", 38, "VERIFY"),
    (5, "MECH", "LOW", "样品仓门闭合不严，存在卡顿", "检验科-郑技师", "13900002206", 22, "DONE"),
    (6, "OTHER", "LOW", "机身外壳破损，需更换面板", "超声科-冯医师", "13900002207", 13, "DONE"),
    (7, "SOFT", "HIGH", "超声成像出现条纹干扰，疑似探头故障", "超声科-何医师", "13900002208", 2, "DOING"),
    (8, "ELEC", "URGENT", "除颤充能超时，无法完成自检", "心内科-许护士", "13900002209", 1, "DOING"),
    (9, "PARA", "MID", "体温监测值与水银温度计偏差 0.8℃", "呼吸科-曹护士", "13900002210", 0, "DISPATCH"),
    (10, "MECH", "LOW", "床体升降不顺畅，需要润滑保养", "康复科-邓技师", "13900002211", 5, "WAIT"),
    (11, "OTHER", "MID", "设备铭牌脱落，需补贴资产标签", "门诊办-姚护士", "13900002212", 6, "VOID"),
)


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


def calc_hours_between(begin_text, end_text):
    """计算两个时间文本之间的小时数，解析失败返回 0。"""
    begin = parse_datetime_text(begin_text)
    end = parse_datetime_text(end_text)
    if begin is None or end is None:
        return 0.0
    return round((end - begin).total_seconds() / 3600.0, 2)


def judge_limit_hours(fault_level):
    """按紧急程度返回要求完成时限小时数，未知等级取一般时限。"""
    return LEVEL_LIMIT_HOURS.get((fault_level or "").strip(), LEVEL_LIMIT_HOURS["LOW"])


def build_order_code(seq_no):
    """按日期与序号拼装工单编号，如 RO-20261031-001。"""
    return "RO-%s-%03d" % (today_date().strftime("%Y%m%d"), seq_no)


def build_dispatch_code(seq_no):
    """按日期与序号拼装派工编号，如 DS-20261031-001。"""
    return "DS-%s-%03d" % (today_date().strftime("%Y%m%d"), seq_no)


def build_feedback_code(seq_no):
    """按日期与序号拼装反馈编号，如 FB-20261031-001。"""
    return "FB-%s-%03d" % (today_date().strftime("%Y%m%d"), seq_no)


def next_seq_no(table_name):
    """读取指定业务表的记录数并加一，用于拼装业务编码。"""
    conn = get_connection()
    try:
        amount = conn.execute(
            "SELECT COUNT(1) AS c FROM %s" % table_name).fetchone()["c"]
    finally:
        conn.close()
    return amount + 1


def fetch_order_row(order_code):
    """按工单编号读取工单记录，未找到返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_repair_order WHERE order_code=?",
            (order_code,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def fetch_dispatch_row(dispatch_code):
    """按派工编号读取派工记录，未找到返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_repair_dispatch WHERE dispatch_code=?",
            (dispatch_code,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def fetch_device_options(limit=60):
    """读取台账设备候选，供报修登记弹窗选择故障设备。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT asset_code, asset_name, dept_name FROM md_asset_ledger "
            "ORDER BY asset_id ASC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(item) for item in rows]


def fetch_spare_options(limit=60):
    """读取备件候选，供完工反馈登记领用备件。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT spare_code, spare_name, unit_name, current_stock, "
            "stock_price FROM md_spare_profile ORDER BY spare_id LIMIT ?",
            (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(item) for item in rows]


# ------------------------------------------------------------------
# 报修登记
# ------------------------------------------------------------------
def create_repair_order(device_code, fault_type="MECH", fault_level="LOW",
                        fault_desc="", report_person="", report_phone="",
                        report_time="", dept_name="", device_name="",
                        remark_text="", operator="admin"):
    """登记一张设备维修工单，并按紧急度自动推算要求完成时间。

    设备编号为空时拒绝登记；未指定报修时间时取当前时间；设备名称与所属
    科室由台账自动带出，避免手工填写造成的口径不一致。

    :return: 含 success、message 与 order_code 的执行结果字典
    """
    device_code = (device_code or "").strip()
    if not device_code:
        return {"success": False, "message": "请先选择报修设备"}
    if fault_type not in FAULT_TYPE_DICT:
        return {"success": False, "message": "故障类型 %s 不合法" % fault_type}
    if fault_level not in FAULT_LEVEL_DICT:
        return {"success": False, "message": "紧急程度 %s 不合法" % fault_level}

    conn = get_connection()
    try:
        asset_row = conn.execute(
            "SELECT asset_name, dept_name FROM md_asset_ledger "
            "WHERE asset_code=?", (device_code,)).fetchone()
        if asset_row is None:
            return {"success": False, "message": "设备编号 %s 不存在" % device_code}
        name = (device_name or "").strip() or asset_row["asset_name"]
        dept = (dept_name or "").strip() or asset_row["dept_name"]
        stamp = parse_datetime_text(report_time) or datetime.datetime.now()
        limit_hours = judge_limit_hours(fault_level)
        expect = stamp + datetime.timedelta(hours=limit_hours)
        order_code = build_order_code(next_seq_no("md_repair_order"))
        conn.execute(
            "INSERT INTO md_repair_order (order_code, device_code, device_name, "
            "dept_name, fault_type, fault_level, fault_desc, report_person, "
            "report_phone, report_time, limit_hours, expect_finish, order_state, "
            "remark_text, operator_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'WAIT',?,?)",
            (order_code, device_code, name, dept, fault_type, fault_level,
             (fault_desc or "").strip(), (report_person or "").strip(),
             (report_phone or "").strip(),
             stamp.strftime("%Y-%m-%d %H:%M:%S"), limit_hours,
             expect.strftime("%Y-%m-%d %H:%M:%S"), (remark_text or "").strip(),
             (operator or "admin").strip()))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("维修工单", "报修登记", order_code,
                      "登记 %s 的 %s 报修工单" % (name, FAULT_TYPE_DICT[fault_type]),
                      operator)
    return {"success": True, "message": "报修工单 %s 已登记" % order_code,
            "order_code": order_code}


# ------------------------------------------------------------------
# 派工与接单
# ------------------------------------------------------------------
def dispatch_repair_order(order_code, engineer_name, engineer_phone="",
                          dispatch_time="", dispatch_note="", operator="admin"):
    """为工单派工：登记派工记录并把工单状态推进为已派工。

    已作废或已验收的工单不允许再派工；派工时间留空时取当前时间。

    :return: 含 success、message 与 dispatch_code 的执行结果字典
    """
    row = fetch_order_row((order_code or "").strip())
    if row is None:
        return {"success": False, "message": "维修工单不存在"}
    if row["order_state"] in ("VOID", "VERIFY", "CLOSE"):
        return {"success": False, "message": "该工单已结束，无法派工"}
    if not (engineer_name or "").strip():
        return {"success": False, "message": "请选择派工工程师"}

    stamp = parse_datetime_text(dispatch_time) or datetime.datetime.now()
    conn = get_connection()
    try:
        dispatch_code = build_dispatch_code(next_seq_no("md_repair_dispatch"))
        conn.execute(
            "INSERT INTO md_repair_dispatch (dispatch_code, order_code, "
            "engineer_name, engineer_phone, dispatch_time, dispatch_state, "
            "dispatch_note, operator_name) VALUES (?,?,?,?,?,'SENT',?,?)",
            (dispatch_code, row["order_code"], engineer_name.strip(),
             (engineer_phone or "").strip(),
             stamp.strftime("%Y-%m-%d %H:%M:%S"),
             (dispatch_note or "").strip(), (operator or "admin").strip()))
        conn.execute(
            "UPDATE md_repair_order SET order_state='DISPATCH' "
            "WHERE order_code=?", (row["order_code"],))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("维修工单", "工单派工", row["order_code"],
                      "派工给 %s" % engineer_name.strip(), operator)
    return {"success": True, "message": "已派工给 %s" % engineer_name.strip(),
            "dispatch_code": dispatch_code}


def accept_dispatch(dispatch_code, accept_time="", operator="admin"):
    """工程师接单：写入接单时间、计算响应时长并把工单推进为维修中。

    :return: 含 success 与 message 的执行结果字典
    """
    row = fetch_dispatch_row((dispatch_code or "").strip())
    if row is None:
        return {"success": False, "message": "派工记录不存在"}
    if row["dispatch_state"] != "SENT":
        return {"success": False, "message": "该派工记录已处理，无需重复接单"}

    order_row = fetch_order_row(row["order_code"])
    if order_row is None:
        return {"success": False, "message": "关联工单不存在"}
    stamp = parse_datetime_text(accept_time) or datetime.datetime.now()
    hours = calc_hours_between(row["dispatch_time"],
                               stamp.strftime("%Y-%m-%d %H:%M:%S"))
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_repair_dispatch SET dispatch_state='ACCEPT', "
            "accept_time=?, response_hours=? WHERE dispatch_code=?",
            (stamp.strftime("%Y-%m-%d %H:%M:%S"), hours, row["dispatch_code"]))
        conn.execute(
            "UPDATE md_repair_order SET order_state='DOING' WHERE order_code=?",
            (row["order_code"],))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("维修工单", "工程师接单", row["order_code"],
                      "%s 已接单，响应时长 %.2f 小时" % (row["engineer_name"], hours),
                      operator)
    return {"success": True, "message": "接单成功，响应时长 %.2f 小时" % hours}


# ------------------------------------------------------------------
# 完工反馈
# ------------------------------------------------------------------
def save_repair_feedback(dispatch_code, result_code="FIXED", root_cause="",
                         repair_method="", spare_code="", spare_amount=0,
                         work_hours=0, repair_cost=0, finish_time="",
                         feedback_note="", operator="admin"):
    """登记完工反馈：记录故障原因与处理措施，并推进工单至已完工。

    登记领用备件时自动写入备件出库流水并扣减库存，实现维修与库存联动；
    维修结论为「建议报废」时同步把台账设备状态改为维修中待处置，便于后续
    进入报废审批流程。

    :return: 含 success、message 与 feedback_code 的执行结果字典
    """
    row = fetch_dispatch_row((dispatch_code or "").strip())
    if row is None:
        return {"success": False, "message": "派工记录不存在"}
    if row["dispatch_state"] not in ("SENT", "ACCEPT"):
        return {"success": False, "message": "该派工记录已完成，无法重复登记"}
    if result_code not in RESULT_CODE_DICT:
        return {"success": False, "message": "维修结论 %s 不合法" % result_code}

    order_row = fetch_order_row(row["order_code"])
    if order_row is None:
        return {"success": False, "message": "关联工单不存在"}
    try:
        amount = int(spare_amount or 0)
        hours = float(work_hours or 0)
        cost = float(repair_cost or 0)
    except (TypeError, ValueError):
        return {"success": False, "message": "备件数量、工时与费用必须为数字"}

    spare_code = (spare_code or "").strip()
    spare_name = ""
    if spare_code:
        if amount <= 0:
            return {"success": False, "message": "领用备件数量必须大于 0"}
        # 联动备件模块登记领用出库，库存不足时由备件模块返回失败原因
        from mdm_spare import save_spare_flow
        spare_row = fetch_spare_row(spare_code)
        if spare_row is None:
            return {"success": False, "message": "备件编码 %s 不存在" % spare_code}
        spare_name = spare_row["spare_name"]
        outcome = save_spare_flow(
            spare_code, "OUT", amount, spare_row["stock_price"],
            order_row["device_code"], order_row["dept_name"], "",
            (finish_time or "")[:10] or date_to_text(today_date()),
            "维修工单 %s 领用" % row["order_code"], operator)
        if not outcome.get("success"):
            return outcome

    stamp = parse_datetime_text(finish_time) or datetime.datetime.now()
    finish_text = stamp.strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        feedback_code = build_feedback_code(next_seq_no("md_repair_feedback"))
        conn.execute(
            "INSERT INTO md_repair_feedback (feedback_code, order_code, "
            "dispatch_code, result_code, root_cause, repair_method, spare_code, "
            "spare_name, spare_amount, work_hours, repair_cost, finish_time, "
            "feedback_note, operator_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (feedback_code, row["order_code"], row["dispatch_code"], result_code,
             (root_cause or "").strip(), (repair_method or "").strip(),
             spare_code, spare_name, amount, hours, cost, finish_text,
             (feedback_note or "").strip(), (operator or "admin").strip()))
        conn.execute(
            "UPDATE md_repair_dispatch SET dispatch_state='FINISH', "
            "finish_time=? WHERE dispatch_code=?",
            (finish_text, row["dispatch_code"]))
        conn.execute(
            "UPDATE md_repair_order SET order_state='DONE', repair_cost="
            "repair_cost+? WHERE order_code=?", (cost, row["order_code"]))
        if result_code == "SCRAP":
            conn.execute(
                "UPDATE md_asset_ledger SET status_code='REPAIR' "
                "WHERE asset_code=?", (order_row["device_code"],))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("维修工单", "完工反馈", row["order_code"],
                      "维修结论 %s，工时 %.1f 小时" % (RESULT_CODE_DICT[result_code], hours),
                      operator)
    return {"success": True, "message": "完工反馈已登记", "feedback_code": feedback_code}


def fetch_spare_row(spare_code):
    """按备件编码读取备件档案，未找到返回 None。"""
    if not spare_code:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT spare_code, spare_name, unit_name, current_stock, "
            "stock_price FROM md_spare_profile WHERE spare_code=?",
            (spare_code,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


# ------------------------------------------------------------------
# 验收与作废
# ------------------------------------------------------------------
def verify_repair_order(order_code, satisfy_score=5, verify_note="",
                        verify_time="", operator="admin"):
    """科室验收：登记满意度与验收意见，并把工单推进为已验收。

    仅已完工的工单可验收；满意度按 1-5 分取值，越界时自动收敛到边界值。

    :return: 含 success 与 message 的执行结果字典
    """
    row = fetch_order_row((order_code or "").strip())
    if row is None:
        return {"success": False, "message": "维修工单不存在"}
    if row["order_state"] != "DONE":
        return {"success": False, "message": "仅已完工的工单可以验收"}
    try:
        score = int(satisfy_score or 5)
    except (TypeError, ValueError):
        score = 5
    if score < 1:
        score = 1
    if score > 5:
        score = 5

    stamp = parse_datetime_text(verify_time) or datetime.datetime.now()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_repair_order SET order_state='VERIFY', satisfy_score=?, "
            "verify_note=?, verify_time=? WHERE order_code=?",
            (score, (verify_note or "").strip(),
             stamp.strftime("%Y-%m-%d %H:%M:%S"), row["order_code"]))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("维修工单", "科室验收", row["order_code"],
                      "验收满意度 %d 分" % score, operator)
    return {"success": True, "message": "工单验收完成，满意度 %d 分" % score}


def cancel_repair_order(order_code, reason_text="", operator="admin"):
    """作废工单：仅未完工的工单可作废，作废后不再参与超时扫描。

    :return: 含 success 与 message 的执行结果字典
    """
    row = fetch_order_row((order_code or "").strip())
    if row is None:
        return {"success": False, "message": "维修工单不存在"}
    if row["order_state"] in ("DONE", "VERIFY", "CLOSE", "VOID"):
        return {"success": False, "message": "已完工或已作废的工单不可重复作废"}
    if not (reason_text or "").strip():
        return {"success": False, "message": "请填写作废原因"}

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_repair_order SET order_state='VOID', remark_text=? "
            "WHERE order_code=?",
            ((reason_text or "").strip(), row["order_code"]))
        conn.execute(
            "UPDATE md_repair_dispatch SET dispatch_state='REJECT' "
            "WHERE order_code=? AND dispatch_state IN ('SENT','ACCEPT')",
            (row["order_code"],))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("维修工单", "工单作废", row["order_code"],
                      "作废原因：%s" % (reason_text or "").strip(), operator)
    return {"success": True, "message": "工单已作废"}


# ------------------------------------------------------------------
# 超时扫描
# ------------------------------------------------------------------
def scan_overdue_repair(operator="admin"):
    """扫描未关闭工单，标记超过要求完成时间的工单并累计超时时长。

    已超时工单的超时时长会随扫描刷新，保证超时看板数据与当前时间同步。

    :return: 本次新标记为超时的工单条数
    """
    now_stamp = datetime.datetime.now()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT order_code, expect_finish, overdue_flag FROM md_repair_order "
            "WHERE order_state IN ('WAIT','DISPATCH','DOING')").fetchall()
        added = 0
        for item in rows:
            expect = parse_datetime_text(item["expect_finish"])
            if expect is None or now_stamp <= expect:
                continue
            hours = round((now_stamp - expect).total_seconds() / 3600.0, 2)
            if not item["overdue_flag"]:
                added += 1
            conn.execute(
                "UPDATE md_repair_order SET overdue_flag=1, overdue_hours=? "
                "WHERE order_code=?", (hours, item["order_code"]))
        conn.commit()
    finally:
        conn.close()
    if added:
        write_ops_journal("维修工单", "超时扫描", "-",
                          "新增超时工单 %d 条" % added, operator)
    return added


# ------------------------------------------------------------------
# 查询
# ------------------------------------------------------------------
def fetch_repair_orders(keyword="", fault_type="", order_state="",
                        fault_level="", overdue_only=False, page=1):
    """按组合条件分页查询维修工单，并附带状态、紧急度与超时标签。"""
    where = []
    params = []
    if keyword:
        where.append("(order_code LIKE ? OR device_name LIKE ? OR dept_name "
                     "LIKE ? OR fault_desc LIKE ? OR report_person LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like, like, like, like])
    if fault_type:
        where.append("fault_type=?")
        params.append(fault_type)
    if order_state:
        where.append("order_state=?")
        params.append(order_state)
    if fault_level:
        where.append("fault_level=?")
        params.append(fault_level)
    if overdue_only:
        where.append("overdue_flag=1")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    if page_no < 1:
        page_no = 1
    offset = (page_no - 1) * ORDER_PAGE_SIZE

    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_repair_order" + clause,
            params).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM md_repair_order" + clause +
            " ORDER BY overdue_flag DESC, order_id DESC LIMIT ? OFFSET ?",
            params + [ORDER_PAGE_SIZE, offset]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        row = dict(item)
        row["type_label"] = FAULT_TYPE_DICT.get(row["fault_type"], row["fault_type"])
        row["level_label"] = FAULT_LEVEL_DICT.get(row["fault_level"], row["fault_level"])
        row["state_label"] = ORDER_STATE_DICT.get(row["order_state"], row["order_state"])
        row["overdue_label"] = "已超时" if row["overdue_flag"] else "正常"
        records.append(row)
    return {
        "records": records,
        "total": total,
        "page": page_no,
        "page_size": ORDER_PAGE_SIZE,
        "page_amount": max(1, (total + ORDER_PAGE_SIZE - 1) // ORDER_PAGE_SIZE),
    }


def fetch_dispatch_rows(order_code="", dispatch_state="", keyword="", page=1):
    """按组合条件分页查询派工记录，并附带关联工单的故障信息。"""
    where = []
    params = []
    if order_code:
        where.append("d.order_code=?")
        params.append(order_code)
    if dispatch_state:
        where.append("d.dispatch_state=?")
        params.append(dispatch_state)
    if keyword:
        where.append("(d.dispatch_code LIKE ? OR d.engineer_name LIKE ? OR "
                     "d.order_code LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like, like])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    if page_no < 1:
        page_no = 1
    offset = (page_no - 1) * DISPATCH_PAGE_SIZE

    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_repair_dispatch d" + clause,
            params).fetchone()["c"]
        rows = conn.execute(
            "SELECT d.*, o.device_name, o.dept_name, o.fault_type, "
            "o.fault_level FROM md_repair_dispatch d LEFT JOIN md_repair_order o "
            "ON d.order_code=o.order_code" + clause +
            " ORDER BY d.dispatch_id DESC LIMIT ? OFFSET ?",
            params + [DISPATCH_PAGE_SIZE, offset]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        row = dict(item)
        row["state_label"] = DISPATCH_STATE_DICT.get(row["dispatch_state"],
                                                     row["dispatch_state"])
        row["type_label"] = FAULT_TYPE_DICT.get(row["fault_type"] or "", "")
        row["level_label"] = FAULT_LEVEL_DICT.get(row["fault_level"] or "", "")
        records.append(row)
    return {
        "records": records,
        "total": total,
        "page": page_no,
        "page_size": DISPATCH_PAGE_SIZE,
        "page_amount": max(1, (total + DISPATCH_PAGE_SIZE - 1) // DISPATCH_PAGE_SIZE),
    }


def fetch_feedback_rows(keyword="", result_code="", limit=30):
    """按条件查询完工反馈记录，并附带维修结论标签。"""
    where = []
    params = []
    if keyword:
        where.append("(feedback_code LIKE ? OR order_code LIKE ? OR "
                     "root_cause LIKE ? OR repair_method LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like, like, like])
    if result_code:
        where.append("result_code=?")
        params.append(result_code)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM md_repair_feedback" + clause +
            " ORDER BY feedback_id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        row = dict(item)
        row["result_label"] = RESULT_CODE_DICT.get(row["result_code"], row["result_code"])
        records.append(row)
    return records


# ------------------------------------------------------------------
# 统计
# ------------------------------------------------------------------
def build_repair_summary():
    """汇总维修工单核心指标：工单量、在修量、超时量、完工率、费用与满意度。"""
    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_repair_order").fetchone()["c"]
        state_rows = conn.execute(
            "SELECT order_state, COUNT(1) AS c FROM md_repair_order "
            "GROUP BY order_state").fetchall()
        overdue = conn.execute(
            "SELECT COUNT(1) AS c FROM md_repair_order WHERE overdue_flag=1"
        ).fetchone()["c"]
        cost_row = conn.execute(
            "SELECT COALESCE(SUM(repair_cost),0) AS s FROM md_repair_order"
        ).fetchone()
        score_row = conn.execute(
            "SELECT COALESCE(SUM(satisfy_score),0) AS s, COUNT(1) AS c "
            "FROM md_repair_order WHERE satisfy_score>0").fetchone()
        hour_row = conn.execute(
            "SELECT COALESCE(SUM(work_hours),0) AS s FROM md_repair_feedback"
        ).fetchone()
        response_row = conn.execute(
            "SELECT COALESCE(AVG(response_hours),0) AS a FROM md_repair_dispatch "
            "WHERE response_hours>0").fetchone()
    finally:
        conn.close()

    state_counter = {item["order_state"]: item["c"] for item in state_rows}
    doing = sum(state_counter.get(code, 0) for code in OPEN_STATE_LIST)
    closed = sum(state_counter.get(code, 0) for code in CLOSED_STATE_LIST)
    finish_rate = round(closed * 100.0 / total, 1) if total else 0.0
    avg_score = round(score_row["s"] * 1.0 / score_row["c"], 2) if score_row["c"] else 0.0
    return {
        "order_total": total,
        "doing_amount": doing,
        "wait_amount": state_counter.get("WAIT", 0),
        "overdue_amount": overdue,
        "closed_amount": closed,
        "finish_rate": finish_rate,
        "repair_cost": round(cost_row["s"], 2),
        "work_hours": round(hour_row["s"], 2),
        "avg_response": round(response_row["a"], 2),
        "avg_score": avg_score,
    }


def build_repair_charts():
    """构建五类图表数据：故障类型分布、工单状态分布、紧急度分布、维修人员
    工单量排行与近 6 个月报修完工趋势。"""
    conn = get_connection()
    try:
        order_rows = conn.execute(
            "SELECT order_code, fault_type, fault_level, order_state, "
            "dept_name, report_time, verify_time FROM md_repair_order").fetchall()
        dispatch_rows = conn.execute(
            "SELECT engineer_name, dispatch_state FROM md_repair_dispatch").fetchall()
    finally:
        conn.close()

    type_counter = {}
    for item in order_rows:
        type_counter[item["fault_type"]] = type_counter.get(item["fault_type"], 0) + 1
    fault_distribution = [
        {"name": FAULT_TYPE_DICT.get(code, code), "value": amount}
        for code, amount in type_counter.items()
    ]

    state_counter = {}
    for item in order_rows:
        state_counter[item["order_state"]] = state_counter.get(item["order_state"], 0) + 1
    state_distribution = [
        {"name": ORDER_STATE_DICT.get(code, code), "value": state_counter.get(code, 0)}
        for code in ("WAIT", "DISPATCH", "DOING", "DONE", "VERIFY", "CLOSE", "VOID")
    ]

    level_counter = {}
    for item in order_rows:
        level_counter[item["fault_level"]] = level_counter.get(item["fault_level"], 0) + 1
    level_distribution = [
        {"name": FAULT_LEVEL_DICT.get(code, code), "value": level_counter.get(code, 0)}
        for code in ("URGENT", "HIGH", "MID", "LOW")
    ]

    engineer_counter = {}
    finish_counter = {}
    for item in dispatch_rows:
        name = (item["engineer_name"] or "").strip() or "未指派"
        engineer_counter[name] = engineer_counter.get(name, 0) + 1
        if item["dispatch_state"] == "FINISH":
            finish_counter[name] = finish_counter.get(name, 0) + 1
    engineer_rank = []
    for name, amount in engineer_counter.items():
        engineer_rank.append({
            "name": name,
            "value": amount,
            "finish": finish_counter.get(name, 0),
        })
    engineer_rank.sort(key=lambda x: (x["value"], x["finish"]), reverse=True)

    month_keys = []
    cursor = today_date().replace(day=1)
    for _ in range(6):
        month_keys.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - datetime.timedelta(days=1)).replace(day=1)
    month_keys.reverse()
    report_counter = {key: 0 for key in month_keys}
    finish_counter_month = {key: 0 for key in month_keys}
    for item in order_rows:
        day = parse_datetime_text(item["report_time"])
        if day is None:
            continue
        key = day.strftime("%Y-%m")
        if key not in report_counter:
            continue
        report_counter[key] += 1
        end_day = parse_datetime_text(item["verify_time"])
        if end_day is None:
            end_day = parse_datetime_text(item["report_time"])
        if end_day is not None:
            end_key = end_day.strftime("%Y-%m")
            if end_key in finish_counter_month and item["order_state"] in CLOSED_STATE_LIST:
                finish_counter_month[end_key] += 1

    dept_counter = {}
    for item in order_rows:
        dept = (item["dept_name"] or "").strip() or "未指定科室"
        dept_counter[dept] = dept_counter.get(dept, 0) + 1
    dept_rank = sorted(
        [{"name": key, "value": value} for key, value in dept_counter.items()],
        key=lambda x: x["value"], reverse=True)[:6]

    return {
        "fault_distribution": fault_distribution,
        "state_distribution": state_distribution,
        "level_distribution": level_distribution,
        "engineer_rank": engineer_rank[:6],
        "dept_rank": dept_rank,
        "month_trend": {
            "months": month_keys,
            "report_values": [report_counter[key] for key in month_keys],
            "finish_values": [finish_counter_month[key] for key in month_keys],
        },
    }


def fetch_repair_options():
    """维修工单页下拉选项：各类字典项、设备候选、备件候选与工程师候选。"""
    return {
        "device_list": fetch_device_options(),
        "spare_list": fetch_spare_options(),
        "engineer_list": [{"name": name, "phone": phone}
                          for name, phone in ENGINEER_LIST],
        "type_options": [{"code": k, "label": v} for k, v in FAULT_TYPE_DICT.items()],
        "level_options": [{"code": k, "label": v} for k, v in FAULT_LEVEL_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in ORDER_STATE_DICT.items()],
        "dispatch_options": [{"code": k, "label": v}
                             for k, v in DISPATCH_STATE_DICT.items()],
        "result_options": [{"code": k, "label": v} for k, v in RESULT_CODE_DICT.items()],
    }


def build_repair_payload(keyword="", fault_type="", order_state="",
                         fault_level="", overdue_only=False,
                         dispatch_order="", dispatch_state="",
                         feedback_keyword="", feedback_result="",
                         order_page=1, dispatch_page=1):
    """组装设备维修工单与派工管理页面所需的全部数据。"""
    scan_overdue_repair()
    order_pager = fetch_repair_orders(keyword, fault_type, order_state,
                                      fault_level, overdue_only, order_page)
    dispatch_pager = fetch_dispatch_rows(dispatch_order, dispatch_state,
                                         "", dispatch_page)
    return {
        "summary": build_repair_summary(),
        "charts": build_repair_charts(),
        "orders": order_pager["records"],
        "order_total": order_pager["total"],
        "order_page": order_pager["page"],
        "order_page_amount": order_pager["page_amount"],
        "dispatchs": dispatch_pager["records"],
        "dispatch_total": dispatch_pager["total"],
        "dispatch_page": dispatch_pager["page"],
        "dispatch_page_amount": dispatch_pager["page_amount"],
        "feedbacks": fetch_feedback_rows(feedback_keyword, feedback_result),
        "options": fetch_repair_options(),
        "result": "OK",
    }


def build_repair_brief():
    """维修工单看板摘要，供运维总览看板嵌入。"""
    summary = build_repair_summary()
    return {
        "order_total": summary["order_total"],
        "doing_amount": summary["doing_amount"],
        "wait_amount": summary["wait_amount"],
        "overdue_amount": summary["overdue_amount"],
        "finish_rate": summary["finish_rate"],
        "repair_cost": summary["repair_cost"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_repair():
    """灌入维修工单演示数据：报修登记、派工接单、完工反馈与科室验收。

    :return: 生成的工单条数
    """
    conn = get_connection()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM md_repair_order").fetchone()["c"]
        if already:
            return 0
        device_rows = conn.execute(
            "SELECT asset_code, asset_name, dept_name FROM md_asset_ledger "
            "ORDER BY asset_id").fetchall()
        spare_rows = conn.execute(
            "SELECT spare_code, spare_name, stock_price FROM md_spare_profile "
            "ORDER BY spare_id").fetchall()
    finally:
        conn.close()
    if not device_rows:
        return 0

    engineer_cycle = [name for name, _ in ENGINEER_LIST]
    spare_codes = [item["spare_code"] for item in spare_rows]
    amount = 0
    for index, (device_index, fault_type, fault_level, fault_desc,
                report_person, report_phone, days_ago, target_state) \
            in enumerate(DEMO_REPAIR_LIST):
        device_row = device_rows[device_index % len(device_rows)]
        report_stamp = datetime.datetime.now() - datetime.timedelta(
            days=days_ago, hours=index % 5)
        outcome = create_repair_order(
            device_row["asset_code"], fault_type, fault_level, fault_desc,
            report_person, report_phone,
            report_stamp.strftime("%Y-%m-%d %H:%M:%S"),
            device_row["dept_name"], device_row["asset_name"],
            "演示维修工单", "医工科-周工")
        if not outcome.get("success"):
            continue
        amount += 1
        order_code = outcome["order_code"]
        if target_state == "VOID":
            cancel_repair_order(order_code, "重复报修，已合并处理", "医工科-周工")
            continue
        if target_state == "WAIT":
            continue

        engineer = engineer_cycle[index % len(engineer_cycle)]
        dispatch_stamp = report_stamp + datetime.timedelta(hours=1 + index % 3)
        dispatch_out = dispatch_repair_order(
            order_code, engineer, "1380000110%d" % (index % 5 + 1),
            dispatch_stamp.strftime("%Y-%m-%d %H:%M:%S"),
            "安排 %s 现场处理" % engineer, "医工科-周工")
        if not dispatch_out.get("success"):
            continue
        if target_state == "DISPATCH":
            continue

        accept_stamp = dispatch_stamp + datetime.timedelta(minutes=20 + index * 5)
        accept_dispatch(dispatch_out["dispatch_code"],
                        accept_stamp.strftime("%Y-%m-%d %H:%M:%S"), engineer)
        if target_state == "DOING":
            continue

        finish_stamp = accept_stamp + datetime.timedelta(hours=2 + index % 6)
        use_spare = spare_codes[index % len(spare_codes)] if spare_codes else ""
        save_repair_feedback(
            dispatch_out["dispatch_code"],
            "FIXED" if index % 5 else "PARTIAL",
            "经排查为%s部件老化导致" % ("电源" if fault_type == "ELEC" else "机械"),
            "更换部件后重新校准并试运行正常",
            use_spare, 1 + index % 2, 1.5 + (index % 4) * 0.5,
            260.0 + index * 45, finish_stamp.strftime("%Y-%m-%d %H:%M:%S"),
            "演示完工反馈", engineer)
        if target_state == "DONE":
            continue

        verify_stamp = finish_stamp + datetime.timedelta(hours=3)
        verify_repair_order(
            order_code, 4 + index % 2, "设备恢复正常运行，使用科室确认",
            verify_stamp.strftime("%Y-%m-%d %H:%M:%S"), "医工科-周工")
        if target_state == "CLOSE":
            conn = get_connection()
            try:
                conn.execute(
                    "UPDATE md_repair_order SET order_state='CLOSE' "
                    "WHERE order_code=?", (order_code,))
                conn.commit()
            finally:
                conn.close()
    scan_overdue_repair()
    return amount

# --------------------------------------------------------------------------
# 第 11 天数据表脚本增量：mdm_schema.sql（M，已内联为字符串常量 DAY11_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY11_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 11 天新增：设备维修工单表
-- ==================================================================
-- 维修工单表：按「一次报修一张工单」记录故障设备、故障类型与紧急度、报修人
-- 与报修时间，并按紧急度自动推算要求完成时限；工单状态随派工、完工、验收
-- 逐级流转，超时扫描写入超时标记与超时时长，形成报修受理的全过程留痕
CREATE TABLE IF NOT EXISTS md_repair_order (
    order_id       INTEGER PRIMARY KEY AUTOINCREMENT,  -- 工单主键
    order_code     TEXT    NOT NULL UNIQUE,            -- 工单编号（RO-年月日-序号）
    device_code    TEXT    NOT NULL,                   -- 设备编号
    device_name    TEXT    DEFAULT '',                 -- 设备名称快照
    dept_name      TEXT    DEFAULT '',                 -- 报修科室
    fault_type     TEXT    DEFAULT 'MECH',             -- 故障类型（见 FAULT_TYPE_DICT）
    fault_level    TEXT    DEFAULT 'LOW',              -- 紧急程度（见 FAULT_LEVEL_DICT）
    fault_desc     TEXT    DEFAULT '',                 -- 故障现象描述
    report_person  TEXT    DEFAULT '',                 -- 报修人
    report_phone   TEXT    DEFAULT '',                 -- 报修联系电话
    report_time    TEXT    DEFAULT '',                 -- 报修时间
    limit_hours    INTEGER DEFAULT 72,                 -- 要求完成时限（小时）
    expect_finish  TEXT    DEFAULT '',                 -- 要求完成时间
    order_state    TEXT    DEFAULT 'WAIT',             -- 工单状态（见 ORDER_STATE_DICT）
    overdue_flag   INTEGER DEFAULT 0,                  -- 超时标记（0=未超时 1=已超时）
    overdue_hours  REAL    DEFAULT 0,                  -- 超时时长（小时）
    repair_cost    REAL    DEFAULT 0,                  -- 累计维修费用（元）
    satisfy_score  INTEGER DEFAULT 0,                  -- 验收满意度（1-5 分）
    verify_note    TEXT    DEFAULT '',                 -- 验收意见
    verify_time    TEXT    DEFAULT '',                 -- 验收时间
    remark_text    TEXT    DEFAULT '',                 -- 工单备注
    operator_name  TEXT    DEFAULT 'admin',            -- 工单受理人
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 11 天新增：维修派工记录表
-- ==================================================================
-- 维修派工表：按「一张工单一次派工」记录派工工程师、派工时间与接单时间，
-- 并自动计算响应时长；工程师接单后工单进入维修中，完工后回写完成时间，
-- 支撑派工响应效率与维修人员工作量的量化评价
CREATE TABLE IF NOT EXISTS md_repair_dispatch (
    dispatch_id    INTEGER PRIMARY KEY AUTOINCREMENT,  -- 派工主键
    dispatch_code  TEXT    NOT NULL UNIQUE,            -- 派工编号（DS-年月日-序号）
    order_code     TEXT    NOT NULL,                   -- 关联工单编号
    engineer_name  TEXT    DEFAULT '',                 -- 派工工程师
    engineer_phone TEXT    DEFAULT '',                 -- 工程师联系电话
    dispatch_time  TEXT    DEFAULT '',                 -- 派工时间
    accept_time    TEXT    DEFAULT '',                 -- 接单时间
    finish_time    TEXT    DEFAULT '',                 -- 完工时间
    response_hours REAL    DEFAULT 0,                  -- 响应时长（小时）
    dispatch_state TEXT    DEFAULT 'SENT',             -- 派工状态（见 DISPATCH_STATE_DICT）
    dispatch_note  TEXT    DEFAULT '',                 -- 派工说明
    operator_name  TEXT    DEFAULT 'admin',            -- 派工人
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 11 天新增：维修完工反馈表
-- ==================================================================
-- 完工反馈表：按「一次派工一条反馈」记录故障原因、处理措施、耗用工时与维修
-- 费用；登记领用备件时自动写入备件出库流水并扣减库存，实现维修与库存联动，
-- 维修结论为建议报废时可联动设备台账进入报废审批流程
CREATE TABLE IF NOT EXISTS md_repair_feedback (
    feedback_id    INTEGER PRIMARY KEY AUTOINCREMENT,  -- 反馈主键
    feedback_code  TEXT    NOT NULL UNIQUE,            -- 反馈编号（FB-年月日-序号）
    order_code     TEXT    NOT NULL,                   -- 关联工单编号
    dispatch_code  TEXT    NOT NULL,                   -- 关联派工编号
    result_code    TEXT    DEFAULT 'FIXED',            -- 维修结论（见 RESULT_CODE_DICT）
    root_cause     TEXT    DEFAULT '',                 -- 故障原因分析
    repair_method  TEXT    DEFAULT '',                 -- 处理措施
    spare_code     TEXT    DEFAULT '',                 -- 领用备件编码
    spare_name     TEXT    DEFAULT '',                 -- 领用备件名称快照
    spare_amount   INTEGER DEFAULT 0,                  -- 领用备件数量
    work_hours     REAL    DEFAULT 0,                  -- 耗用工时（小时）
    repair_cost    REAL    DEFAULT 0,                  -- 本次维修费用（元）
    finish_time    TEXT    DEFAULT '',                 -- 完工时间
    feedback_note  TEXT    DEFAULT '',                 -- 反馈说明
    operator_name  TEXT    DEFAULT '',                 -- 反馈填报人
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_rorder_code      ON md_repair_order (order_code);
CREATE INDEX IF NOT EXISTS idx_rorder_device    ON md_repair_order (device_code);
CREATE INDEX IF NOT EXISTS idx_rorder_dept      ON md_repair_order (dept_name);
CREATE INDEX IF NOT EXISTS idx_rorder_state     ON md_repair_order (order_state);
CREATE INDEX IF NOT EXISTS idx_rorder_level     ON md_repair_order (fault_level);
CREATE INDEX IF NOT EXISTS idx_rdispatch_code   ON md_repair_dispatch (dispatch_code);
CREATE INDEX IF NOT EXISTS idx_rdispatch_order  ON md_repair_dispatch (order_code);
CREATE INDEX IF NOT EXISTS idx_rdispatch_eng    ON md_repair_dispatch (engineer_name);
CREATE INDEX IF NOT EXISTS idx_rdispatch_state  ON md_repair_dispatch (dispatch_state);
CREATE INDEX IF NOT EXISTS idx_rfeedback_code   ON md_repair_feedback (feedback_code);
CREATE INDEX IF NOT EXISTS idx_rfeedback_order  ON md_repair_feedback (order_code);
CREATE INDEX IF NOT EXISTS idx_rfeedback_result ON md_repair_feedback (result_code);
"""


def apply_day11_schema(conn):
    """把第 11 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY11_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 11 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-31（第 11 天 / 共 15 天）
#           供应商合同管理、备件耗材库存管理与设备维修工单派工管理十一套页面
#           路由，并提供台账增删改查、闲置识别扫描、状态切换、ROI 测算与年度
#           收支档案维护、盘点任务生成与账实核对、维保登记、运维日志检索登记
#           闭环与 CSV 导出、报废申请发起逐级审批流转与处置归档、校准计划编制、
#           校准结果登记、证书到期扫描与校准档案查询、供应商档案登记、合同
#           签订与付款节点拆分执行、合同到期扫描与供应商履约评级重算、备件耗材
#           档案登记、采购入库与领用出库流水登记、库存状态回算、低储扫描生成
#           补货计划、补货下单与到货入库确认，以及维修工单报修登记、按紧急度
#           推算完成时限、工程师派工接单、完工反馈登记领用备件、科室验收评价、
#           工单作废与超时扫描等 RESTful 接口，供前端表格、弹窗与

from mdm_repair import (
    build_repair_payload,
    build_repair_brief,
    create_repair_order,
    dispatch_repair_order,
    accept_dispatch,
    save_repair_feedback,
    verify_repair_order,
    cancel_repair_order,
    scan_overdue_repair,
    fetch_repair_orders,
    fetch_dispatch_rows,
    fetch_feedback_rows,
    seed_demo_repair,
    FAULT_TYPE_DICT,
    FAULT_LEVEL_DICT,
    ORDER_STATE_DICT,
    DISPATCH_STATE_DICT,
    RESULT_CODE_DICT,
)

# 【函数内插入】def api_overview():
#    # 第 10 天新增：总览叠加备件库存摘要，供备件概况卡渲染
#    # 第 11 天新增：总览叠加维修工单摘要，供工单概况卡渲染
#    payload["repair_brief"] = build_repair_brief()

@app.route("/md/repair")
def panel_repair():
    """维修工单与派工管理页面：工单概览、五类图表、工单派工与完工反馈。"""
    write_ops_journal("维修工单", "查询", "PANEL-REPAIR", "打开设备维修工单与派工管理页面")
    return render_template(
        "panel_repair.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/md/repair/panel")
def api_repair_panel():
    """维修工单数据接口：汇总指标、五类图表、双分页明细、反馈与选项。"""
    return jsonify(build_repair_payload(
        keyword=(request.args.get("keyword") or "").strip(),
        fault_type=(request.args.get("fault_type") or "").strip(),
        order_state=(request.args.get("order_state") or "").strip(),
        fault_level=(request.args.get("fault_level") or "").strip(),
        overdue_only=(request.args.get("overdue_only") or "") == "1",
        dispatch_order=(request.args.get("dispatch_order") or "").strip(),
        dispatch_state=(request.args.get("dispatch_state") or "").strip(),
        feedback_keyword=(request.args.get("feedback_keyword") or "").strip(),
        feedback_result=(request.args.get("feedback_result") or "").strip(),
        order_page=int(request.args.get("order_page", 1)),
        dispatch_page=int(request.args.get("dispatch_page", 1)),
    ))


@app.route("/api/md/repair/order/create", methods=["POST"])
def api_repair_order_create():
    """报修登记接口：登记维修工单并按紧急度自动推算要求完成时间。"""
    body = request.get_json(silent=True) or {}
    result = create_repair_order(
        (body.get("device_code") or "").strip(),
        (body.get("fault_type") or "MECH").strip(),
        (body.get("fault_level") or "LOW").strip(),
        body.get("fault_desc") or "",
        body.get("report_person") or "",
        body.get("report_phone") or "",
        (body.get("report_time") or "").strip(),
        body.get("dept_name") or "",
        body.get("device_name") or "",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/repair/dispatch/create", methods=["POST"])
def api_repair_dispatch_create():
    """工单派工接口：登记派工记录并把工单推进为已派工。"""
    body = request.get_json(silent=True) or {}
    result = dispatch_repair_order(
        (body.get("order_code") or "").strip(),
        (body.get("engineer_name") or "").strip(),
        body.get("engineer_phone") or "",
        (body.get("dispatch_time") or "").strip(),
        body.get("dispatch_note") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/repair/dispatch/accept", methods=["POST"])
def api_repair_dispatch_accept():
    """工程师接单接口：写入接单时间、计算响应时长并推进工单为维修中。"""
    body = request.get_json(silent=True) or {}
    result = accept_dispatch(
        (body.get("dispatch_code") or "").strip(),
        (body.get("accept_time") or "").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/repair/feedback/save", methods=["POST"])
def api_repair_feedback_save():
    """完工反馈接口：登记故障原因与处理措施，领用备件时联动扣减库存。"""
    body = request.get_json(silent=True) or {}
    result = save_repair_feedback(
        (body.get("dispatch_code") or "").strip(),
        (body.get("result_code") or "FIXED").strip(),
        body.get("root_cause") or "",
        body.get("repair_method") or "",
        (body.get("spare_code") or "").strip(),
        body.get("spare_amount") or 0,
        body.get("work_hours") or 0,
        body.get("repair_cost") or 0,
        (body.get("finish_time") or "").strip(),
        body.get("feedback_note") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/repair/verify", methods=["POST"])
def api_repair_verify():
    """科室验收接口：登记满意度与验收意见，并把工单推进为已验收。"""
    body = request.get_json(silent=True) or {}
    result = verify_repair_order(
        (body.get("order_code") or "").strip(),
        body.get("satisfy_score") or 5,
        body.get("verify_note") or "",
        (body.get("verify_time") or "").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/repair/cancel", methods=["POST"])
def api_repair_cancel():
    """工单作废接口：作废未完工工单并退回其派工记录。"""
    body = request.get_json(silent=True) or {}
    result = cancel_repair_order(
        (body.get("order_code") or "").strip(),
        body.get("reason_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/repair/scan", methods=["POST"])
def api_repair_scan():
    """超时扫描接口：标记超过要求完成时间的工单并累计超时时长。"""
    body = request.get_json(silent=True) or {}
    return jsonify({
        "result": "OK",
        "success": True,
        "amount": scan_overdue_repair((body.get("operator") or "admin").strip()),
    })


@app.route("/api/md/repair/options")
def api_repair_options():
    """维修工单下拉选项接口：故障类型、紧急度、工单状态与维修结论字典。"""
    return jsonify({
        "result": "OK",
        "type_options": [{"code": k, "label": v} for k, v in FAULT_TYPE_DICT.items()],
        "level_options": [{"code": k, "label": v} for k, v in FAULT_LEVEL_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in ORDER_STATE_DICT.items()],
        "dispatch_options": [{"code": k, "label": v}
                             for k, v in DISPATCH_STATE_DICT.items()],
        "result_options": [{"code": k, "label": v} for k, v in RESULT_CODE_DICT.items()],
    })



# 【函数内插入】def bootstrap_application():
#    seed_demo_repair()

# 【函数内插入】if __name__ == "__main__":
#    print("维修工单： http://127.0.0.1:%d/md/repair" % PORT)

# --------------------------------------------------------------------------
# 第 11 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/mdm_repair.js             新增 568 行
#   [修改] templates/layout_mdm.html        新增 1 行
#   [新增] templates/panel_repair.html      新增 424 行
