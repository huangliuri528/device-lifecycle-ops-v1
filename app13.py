# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-11-02（第 13 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 13 天新增与修改的源代码，不含第 1~12 天已有代码
#           本次迭代主题：新增设备验收与装机转资管理模块（验收登记、逐项检查、整改复检、阶段流转、归档转资）
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5001/md/overview）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 13 天新增业务模块 1：mdm_accept.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-11-02（第 13 天 / 共 15 天）
# 模块功能：设备验收与装机转资管理模块。实现到货验收单登记（关联采购合同、
#           供货单位、机身序列号、质保月数与验收金额）、按默认验收模板展开
#           六项检查明细、逐项登记检查结论、不合格项整改复检、装机阶段逐级
#           流转、验收结论归档、转资入账回写台账与超期扫描全过程管理；验收
#           通过后自动推算质保到期日期，转资时回写设备采购原值与在用状态并
#           登记状态流转；输出验收类型分布、验收状态分布、检查结论分布、科室
#           验收通过率对比与近 6 个月验收趋势五类统计图表，形成「到货登记 →
#           逐项检查 → 不合格整改 → 阶段流转 → 验收归档 → 转资入账」的
#           设备准入闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from mdm_dal import get_connection, write_ops_journal, append_status_flow

# ------------------------------------------------------------------
# 验收类型字典：区分到货开箱、安装调试、技术性能与转资交付四类验收
# ------------------------------------------------------------------
ACCEPT_TYPE_DICT = {
    "ARRIVE": "到货开箱验收",
    "INSTALL": "安装调试验收",
    "TECH": "技术性能验收",
    "FINAL": "转资交付验收",
}

# 验收状态字典：随检查、归档逐级流转
ACCEPT_STATE_DICT = {
    "WAIT": "待验收",
    "DOING": "验收中",
    "PASS": "验收通过",
    "REJECT": "验收不通过",
    "VOID": "已作废",
}

# 检查结论字典
CHECK_RESULT_DICT = {
    "NOTDO": "未检",
    "PASS": "合格",
    "FAIL": "不合格",
}

# 装机阶段状态字典
STAGE_STATE_DICT = {
    "WAIT": "待执行",
    "DOING": "进行中",
    "DONE": "已完成",
    "SKIP": "已跳过",
}

# 未归档验收状态集合：超期扫描只处理这些状态的验收单
ACCEPT_OPEN_STATE_LIST = ("WAIT", "DOING")

# 默认验收检查项模板：建单时逐条落库，保证全院验收口径统一
# （检查项目，检查方法，合格标准）
DEFAULT_ACCEPT_ITEM_LIST = (
    ("外包装与开箱检查", "检查外包装完整性与运输损伤情况", "包装完好，无挤压变形与淋湿"),
    ("主机与配件清点", "按装箱单逐项清点主机、配件与线缆", "数量型号与装箱单一致"),
    ("铭牌与序列号核对", "核对铭牌型号与机身序列号", "与合同及出厂合格证一致"),
    ("技术性能检测", "按技术要求逐项实测关键性能参数", "各项参数达到技术标书要求"),
    ("电气安全与接地", "检测保护接地阻抗与外壳漏电流", "接地可靠，漏电流在安全限值内"),
    ("随机资料与证书", "清点说明书、合格证、保修卡与注册证", "资料齐全并已归档留存"),
)

# 默认装机阶段模板：（阶段名称，相对到货日的计划完成偏移天数）
DEFAULT_STAGE_LIST = (
    ("到货开箱", 0),
    ("安装就位", 2),
    ("通电调试", 4),
    ("操作培训", 7),
    ("转资入账", 10),
)

# 验收负责人候选，供建单选择与演示数据灌入
ACCEPT_OWNER_LIST = ("医工科-郑工", "设备科-冯工", "采购中心-许主任")

# 分页尺寸：验收单独立分页
ORDER_PAGE_SIZE = 10

# 演示验收单清单
# （验收类型，台账设备序号，到货日期偏移天数，验收周期天数，目标状态，备注）
DEMO_ACCEPT_LIST = (
    ("ARRIVE", 0, -132, 4, "PASS", "新购监护类设备到货开箱验收，资料齐全"),
    ("INSTALL", 1, -98, 6, "PASS", "影像类设备安装就位与通电调试验收"),
    ("TECH", 2, -63, 8, "PASS", "检验类设备技术性能验收，含一次整改复检"),
    ("FINAL", 3, -21, 7, "DOING", "治疗类设备转资交付验收，阶段推进中"),
    ("INSTALL", 4, 6, 6, "WAIT", "近期到货设备，等待安装调试验收"),
    ("ARRIVE", 5, -9, 5, "REJECT", "到货验收发现配件缺失，需供应商整改"),
)

# 演示用供货单位候选
DEMO_VENDOR_LIST = ("通用电气医疗（中国）", "迈瑞生物医疗电子", "东软医疗系统",
                    "联影医疗科技", "鱼跃医疗设备")


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
                table_name.replace("md_accept_", ""), table_name)).fetchone()
    finally:
        conn.close()
    return int(row["m"] or 0) + 1


def build_order_code(seq_no):
    """生成验收单编号，形如 AC-0000007。"""
    return "AC-%07d" % seq_no


def build_check_code(seq_no):
    """生成验收检查项编号，形如 ACK-0000031。"""
    return "ACK-%07d" % seq_no


def build_stage_code(seq_no):
    """生成装机阶段编号，形如 AS-0000019。"""
    return "AS-%07d" % seq_no


def calc_warranty_end(base_date_text, warranty_months):
    """按质保月数推算质保到期日期，基准日期为空时返回空串。"""
    base = parse_date_text(base_date_text)
    if base is None:
        return ""
    months = int(warranty_months or 0)
    if months <= 0:
        return ""
    year = base.year + (base.month - 1 + months) // 12
    month = (base.month - 1 + months) % 12 + 1
    day = base.day
    last_day = [31, 29 if (year % 4 == 0 and year % 100 != 0) or year % 400 == 0
                else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date_to_text(datetime.date(year, month, min(day, last_day)))


def calc_accept_score(pass_amount, fail_amount):
    """按已检项目推算验收得分：合格项占比百分制，未检项不计入分母。"""
    checked = int(pass_amount or 0) + int(fail_amount or 0)
    if checked <= 0:
        return 0.0
    return round1(pass_amount * 100.0 / checked)


# ------------------------------------------------------------------
# 查询辅助
# ------------------------------------------------------------------
def fetch_device_options(limit=80, dept_name=""):
    """读取台账设备候选，供验收建单选择到货设备使用。"""
    dept_name = (dept_name or "").strip()
    sql = ("SELECT asset_code, asset_name, dept_name, vendor_name, purchase_price "
           "FROM md_asset_ledger ")
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


def fetch_order_row(order_code):
    """按验收单编号读取一条验收单，不存在返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_accept_order WHERE order_code=?",
            ((order_code or "").strip(),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def fetch_order_checks(order_code):
    """读取某张验收单下的全部检查明细。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM md_accept_check WHERE order_code=? "
            "ORDER BY check_id ASC", ((order_code or "").strip(),)).fetchall()
    finally:
        conn.close()
    return [dict(item) for item in rows]


def fetch_order_stages(order_code):
    """读取某张验收单下的全部装机阶段。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM md_accept_stage WHERE order_code=? "
            "ORDER BY stage_seq ASC", ((order_code or "").strip(),)).fetchall()
    finally:
        conn.close()
    return [dict(item) for item in rows]


def fetch_contract_options(limit=20):
    """读取采购合同候选，供验收单关联合同选用。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT contract_code, contract_name, vendor_name, warranty_months "
            "FROM md_contract_info WHERE contract_type='PURCHASE' "
            "ORDER BY contract_id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(item) for item in rows]


# ------------------------------------------------------------------
# 验收单登记
# ------------------------------------------------------------------
def save_accept_order(order_code, asset_code, accept_type="ARRIVE",
                      vendor_name="", contract_code="", serial_no="",
                      arrive_date="", plan_date="", accept_amount=0,
                      warranty_months=12, acceptor_name="", remark_text="",
                      operator="admin"):
    """新建或编辑一张到货验收单，新建时自动展开检查项与装机阶段。

    验收单编号留空表示新建；新建时按默认模板生成六项检查明细与五个装机
    阶段，并按质保月数与到货日期推算质保到期日期；已归档或已作废的验收
    单不允许再编辑。

    :return: 含 success、message 与 order_code 的执行结果字典
    """
    asset_code = (asset_code or "").strip()
    if not asset_code:
        return {"success": False, "message": "请选择待验收设备"}
    if accept_type not in ACCEPT_TYPE_DICT:
        return {"success": False, "message": "验收类型 %s 不合法" % accept_type}

    conn = get_connection()
    try:
        asset_row = conn.execute(
            "SELECT asset_code, asset_name, dept_name, vendor_name, purchase_price "
            "FROM md_asset_ledger WHERE asset_code=?", (asset_code,)).fetchone()
        if asset_row is None:
            return {"success": False, "message": "设备 %s 不存在于台账" % asset_code}
    finally:
        conn.close()

    arrive_day = parse_date_text(arrive_date) or today_date()
    arrive_text = date_to_text(arrive_day)
    try:
        cycle_days = int(warranty_months or 12)
    except (TypeError, ValueError):
        cycle_days = 12
    warranty_text = calc_warranty_end(arrive_text, cycle_days)
    plan_text = (plan_date or "").strip() or date_to_text(
        arrive_day + datetime.timedelta(days=10))

    conn = get_connection()
    try:
        exist = None
        if (order_code or "").strip():
            exist = conn.execute(
                "SELECT accept_state FROM md_accept_order WHERE order_code=?",
                (order_code.strip(),)).fetchone()
            if exist is not None and exist["accept_state"] in ("PASS", "REJECT", "VOID"):
                return {"success": False, "message": "已归档或作废的验收单不可再编辑"}
        if exist is None:
            code = build_order_code(next_seq_no("md_accept_order"))
            conn.execute(
                "INSERT INTO md_accept_order (order_code, asset_code, asset_name, "
                "dept_name, vendor_name, contract_code, accept_type, serial_no, "
                "arrive_date, plan_date, accept_amount, warranty_months, "
                "warranty_end, accept_state, stage_name, check_total, "
                "acceptor_name, remark_text, operator_name) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'WAIT',?,?,?,?,?)",
                (code, asset_code, asset_row["asset_name"],
                 asset_row["dept_name"] or "",
                 (vendor_name or "").strip() or (asset_row["vendor_name"] or ""),
                 (contract_code or "").strip(), accept_type,
                 (serial_no or "").strip(), arrive_text, plan_text,
                 float(accept_amount or 0) or float(asset_row["purchase_price"] or 0),
                 cycle_days, warranty_text,
                 DEFAULT_STAGE_LIST[0][0], len(DEFAULT_ACCEPT_ITEM_LIST),
                 (acceptor_name or "").strip(), (remark_text or "").strip(),
                 (operator or "admin").strip()))

            check_seq = next_seq_no("md_accept_check")
            for name, method, standard in DEFAULT_ACCEPT_ITEM_LIST:
                conn.execute(
                    "INSERT INTO md_accept_check (check_code, order_code, "
                    "item_name, check_method, std_value, item_result, "
                    "checker_name) VALUES (?,?,?,?,?,'NOTDO',?)",
                    (build_check_code(check_seq), code, name, method, standard,
                     (acceptor_name or "").strip()))
                check_seq += 1

            stage_seq = next_seq_no("md_accept_stage")
            for index, (stage_name, offset_days) in enumerate(DEFAULT_STAGE_LIST):
                conn.execute(
                    "INSERT INTO md_accept_stage (stage_code, order_code, "
                    "stage_name, stage_seq, plan_date, executor_name, "
                    "stage_state) VALUES (?,?,?,?,?,?,'WAIT')",
                    (build_stage_code(stage_seq), code, stage_name, index + 1,
                     date_to_text(arrive_day + datetime.timedelta(days=offset_days)),
                     (acceptor_name or "").strip()))
                stage_seq += 1
            action = "验收登记"
        else:
            code = order_code.strip()
            conn.execute(
                "UPDATE md_accept_order SET vendor_name=?, contract_code=?, "
                "accept_type=?, serial_no=?, arrive_date=?, plan_date=?, "
                "accept_amount=?, warranty_months=?, warranty_end=?, "
                "acceptor_name=?, remark_text=?, operator_name=? WHERE order_code=?",
                ((vendor_name or "").strip(), (contract_code or "").strip(),
                 accept_type, (serial_no or "").strip(), arrive_text, plan_text,
                 float(accept_amount or 0), cycle_days, warranty_text,
                 (acceptor_name or "").strip(), (remark_text or "").strip(),
                 (operator or "admin").strip(), code))
            action = "验收编辑"
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("设备验收", action, code,
                      "%s 验收单：%s，类型 %s" % (
                          action, asset_row["asset_name"],
                          ACCEPT_TYPE_DICT[accept_type]), operator)
    return {"success": True, "message": "验收单 %s 已保存" % code,
            "order_code": code}


# ------------------------------------------------------------------
# 检查结论登记与整改复检
# ------------------------------------------------------------------
def recalc_order_check(order_code):
    """重算某张验收单的合格项、不合格项与验收得分，并回写当前阶段。"""
    conn = get_connection()
    try:
        stat = conn.execute(
            "SELECT SUM(CASE WHEN item_result='PASS' THEN 1 ELSE 0 END) AS ok, "
            "SUM(CASE WHEN item_result='FAIL' THEN 1 ELSE 0 END) AS bad, "
            "SUM(CASE WHEN item_result='NOTDO' THEN 1 ELSE 0 END) AS todo "
            "FROM md_accept_check WHERE order_code=?", (order_code,)).fetchone()
        order = conn.execute(
            "SELECT accept_state FROM md_accept_order WHERE order_code=?",
            (order_code,)).fetchone()
        pass_amount = int(stat["ok"] or 0)
        fail_amount = int(stat["bad"] or 0)
        todo_amount = int(stat["todo"] or 0)
        new_state = order["accept_state"] if order else "WAIT"
        if order is not None and order["accept_state"] == "WAIT" and (
                pass_amount or fail_amount):
            new_state = "DOING"
        conn.execute(
            "UPDATE md_accept_order SET check_pass=?, check_fail=?, "
            "accept_state=? WHERE order_code=?",
            (pass_amount, fail_amount, new_state, order_code))
        conn.commit()
    finally:
        conn.close()
    return {"pass_amount": pass_amount, "fail_amount": fail_amount,
            "todo_amount": todo_amount,
            "score": calc_accept_score(pass_amount, fail_amount)}


def save_check_result(check_code, check_value="", item_result="PASS",
                      problem_desc="", checker_name="", operator="admin"):
    """登记单条验收检查项的结论，并回算所属验收单的合格项与不合格项。

    结论为不合格时必须填写问题描述；登记后自动重算验收单统计，首次登记
    时验收单自动推进为验收中。

    :return: 含 success 与 message 的执行结果字典
    """
    if item_result not in CHECK_RESULT_DICT:
        return {"success": False, "message": "检查结论 %s 不合法" % item_result}
    if item_result == "FAIL" and not (problem_desc or "").strip():
        return {"success": False, "message": "结论为不合格时请填写问题描述"}
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_accept_check WHERE check_code=?",
            ((check_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "验收检查项不存在"}
        order = conn.execute(
            "SELECT accept_state FROM md_accept_order WHERE order_code=?",
            (row["order_code"],)).fetchone()
        if order is not None and order["accept_state"] in ("PASS", "REJECT", "VOID"):
            return {"success": False, "message": "该验收单已归档，不可再登记结论"}
        conn.execute(
            "UPDATE md_accept_check SET check_value=?, item_result=?, "
            "problem_desc=?, checker_name=? WHERE check_code=?",
            ((check_value or "").strip(), item_result,
             (problem_desc or "").strip(), (checker_name or "").strip(),
             row["check_code"]))
        conn.commit()
    finally:
        conn.close()
    recalc_order_check(row["order_code"])
    write_ops_journal("设备验收", "结论登记", row["check_code"],
                      "登记验收项「%s」结论 %s" % (
                          row["item_name"], CHECK_RESULT_DICT[item_result]),
                      operator)
    return {"success": True, "message": "验收检查项 %s 已登记" % row["check_code"]}


def rectify_check_item(check_code, rectify_note="", check_value="",
                       checker_name="", operator="admin"):
    """对不合格检查项做整改复检：填写整改说明并把结论改判为合格。

    :return: 含 success 与 message 的执行结果字典
    """
    if not (rectify_note or "").strip():
        return {"success": False, "message": "请填写整改复检说明"}
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_accept_check WHERE check_code=?",
            ((check_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "验收检查项不存在"}
        if row["item_result"] != "FAIL":
            return {"success": False, "message": "仅不合格检查项需要整改复检"}
        conn.execute(
            "UPDATE md_accept_check SET item_result='PASS', handle_flag=1, "
            "rectify_note=?, check_value=?, checker_name=? WHERE check_code=?",
            ((rectify_note or "").strip(),
             (check_value or "").strip() or "整改后复检合格",
             (checker_name or "").strip(), row["check_code"]))
        conn.commit()
    finally:
        conn.close()
    recalc_order_check(row["order_code"])
    write_ops_journal("设备验收", "整改复检", row["check_code"],
                      "验收项「%s」整改复检合格：%s" % (
                          row["item_name"], (rectify_note or "").strip()),
                      operator)
    return {"success": True, "message": "检查项 %s 整改复检已通过" % row["check_code"]}


# ------------------------------------------------------------------
# 装机阶段流转
# ------------------------------------------------------------------
def advance_accept_stage(order_code, executor_name="", remark_text="",
                         finish_date="", operator="admin"):
    """推进一张验收单的装机阶段：完成当前阶段并开启下一阶段。

    阶段全部完成后验收单当前阶段标记为「转资入账」已就绪；已归档或作废
    的验收单不再推进。

    :return: 含 success、message 与 stage_name 的执行结果字典
    """
    order = fetch_order_row(order_code)
    if order is None:
        return {"success": False, "message": "验收单不存在"}
    if order["accept_state"] in ("PASS", "REJECT", "VOID"):
        return {"success": False, "message": "已归档或作废的验收单不可推进阶段"}
    stages = fetch_order_stages(order["order_code"])
    if not stages:
        return {"success": False, "message": "该验收单没有装机阶段"}
    target = None
    for stage in stages:
        if stage["stage_state"] in ("WAIT", "DOING"):
            target = stage
            break
    if target is None:
        return {"success": False, "message": "该验收单的装机阶段已全部完成"}

    end_day = parse_date_text(finish_date) or today_date()
    start_day = parse_date_text(order["arrive_date"]) or end_day
    duration = max(0, (end_day - start_day).days)
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_accept_stage SET stage_state='DONE', finish_date=?, "
            "executor_name=?, duration_days=?, remark_text=? WHERE stage_code=?",
            (date_to_text(end_day), (executor_name or "").strip() or
             (order["acceptor_name"] or ""), duration,
             (remark_text or "").strip(), target["stage_code"]))
        next_stage = None
        for stage in stages:
            if stage["stage_seq"] > target["stage_seq"]:
                next_stage = stage
                break
        next_name = next_stage["stage_name"] if next_stage else target["stage_name"]
        if next_stage is not None:
            conn.execute(
                "UPDATE md_accept_stage SET stage_state='DOING' "
                "WHERE stage_code=?", (next_stage["stage_code"],))
        conn.execute(
            "UPDATE md_accept_order SET stage_name=? WHERE order_code=?",
            (next_name, order["order_code"]))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("设备验收", "阶段推进", order["order_code"],
                      "完成装机阶段「%s」，推进至「%s」" % (
                          target["stage_name"], next_name), operator)
    return {"success": True,
            "message": "阶段「%s」已完成，当前阶段 %s" % (target["stage_name"], next_name),
            "stage_name": next_name}


# ------------------------------------------------------------------
# 验收归档与转资
# ------------------------------------------------------------------
def finish_accept_order(order_code, result_code="PASS", accept_date="",
                        accept_amount=0, remark_text="", operator="admin"):
    """归档一张验收单的验收结论：存在未整改不合格项时不允许判为通过。

    :return: 含 success、message 与 score 的执行结果字典
    """
    if result_code not in ("PASS", "REJECT"):
        return {"success": False, "message": "验收结论 %s 不合法" % result_code}
    order = fetch_order_row(order_code)
    if order is None:
        return {"success": False, "message": "验收单不存在"}
    if order["accept_state"] in ("PASS", "REJECT"):
        return {"success": False, "message": "该验收单已归档"}
    if order["accept_state"] == "VOID":
        return {"success": False, "message": "已作废的验收单不可归档"}

    stat = recalc_order_check(order["order_code"])
    if result_code == "PASS" and stat["fail_amount"] > 0:
        return {"success": False,
                "message": "仍有 %d 项不合格未完成整改，不可判为验收通过" %
                           stat["fail_amount"]}
    if result_code == "PASS" and stat["todo_amount"] > 0:
        return {"success": False,
                "message": "仍有 %d 项未检查，请登记完成后再归档" % stat["todo_amount"]}

    end_day = parse_date_text(accept_date) or today_date()
    end_text = date_to_text(end_day)
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_accept_order SET accept_state=?, accept_date=?, "
            "accept_amount=?, remark_text=?, operator_name=? WHERE order_code=?",
            (result_code, end_text,
             float(accept_amount or 0) or float(order["accept_amount"] or 0),
             (remark_text or "").strip(), (operator or "admin").strip(),
             order["order_code"]))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("设备验收", "验收归档", order["order_code"],
                      "验收结论 %s，得分 %s 分" % (
                          ACCEPT_STATE_DICT[result_code], stat["score"]), operator)
    return {"success": True,
            "message": "验收单 %s 已归档为%s" % (
                order["order_code"], ACCEPT_STATE_DICT[result_code]),
            "score": stat["score"]}


def transfer_accept_asset(order_code, transfer_date="", operator="admin"):
    """对验收通过的验收单执行转资入账：回写台账采购原值与在用状态。

    转资成功后登记一条设备状态流转记录，形成「验收通过 → 转资入账 →
    纳入在用设备台账」的闭环；未通过验收或已转资的单据不可重复转资。

    :return: 含 success 与 message 的执行结果字典
    """
    order = fetch_order_row(order_code)
    if order is None:
        return {"success": False, "message": "验收单不存在"}
    if order["accept_state"] != "PASS":
        return {"success": False, "message": "仅验收通过的验收单可以转资入账"}
    if order["transfer_flag"]:
        return {"success": False, "message": "该验收单已完成转资入账"}

    end_day = parse_date_text(transfer_date) or today_date()
    end_text = date_to_text(end_day)
    conn = get_connection()
    try:
        asset_row = conn.execute(
            "SELECT asset_id, asset_code, current_status FROM md_asset_ledger "
            "WHERE asset_code=?", (order["asset_code"],)).fetchone()
        if asset_row is None:
            return {"success": False, "message": "验收设备在台账中不存在"}
        from_status = asset_row["current_status"] or "STOCK"
        amount = float(order["accept_amount"] or 0)
        conn.execute(
            "UPDATE md_asset_ledger SET current_status='IN_USE', "
            "purchase_date=CASE WHEN purchase_date='' THEN ? ELSE purchase_date END, "
            "purchase_price=CASE WHEN ? > 0 THEN ? ELSE purchase_price END "
            "WHERE asset_code=?",
            (end_text, amount, amount, order["asset_code"]))
        conn.execute(
            "UPDATE md_accept_order SET transfer_flag=1, transfer_date=? "
            "WHERE order_code=?", (end_text, order["order_code"]))
        conn.commit()
    finally:
        conn.close()
    if from_status != "IN_USE":
        append_status_flow(asset_row["asset_id"], from_status, "IN_USE",
                           "验收单 %s 转资入账" % order["order_code"], operator)
    write_ops_journal("设备验收", "转资入账", order["order_code"],
                      "设备 %s 完成转资入账，确认金额 %.2f 元" % (
                          order["asset_name"], float(order["accept_amount"] or 0)),
                      operator)
    return {"success": True,
            "message": "验收单 %s 已转资入账" % order["order_code"]}


def cancel_accept_order(order_code, reason_text="", operator="admin"):
    """作废一张验收单：已归档通过的验收单不可作废。"""
    order = fetch_order_row(order_code)
    if order is None:
        return {"success": False, "message": "验收单不存在"}
    if order["accept_state"] == "PASS":
        return {"success": False, "message": "已验收通过的验收单不可作废"}
    if order["accept_state"] == "VOID":
        return {"success": False, "message": "该验收单已作废"}
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_accept_order SET accept_state='VOID', remark_text=? "
            "WHERE order_code=?",
            ((reason_text or "").strip(), order["order_code"]))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("设备验收", "验收作废", order["order_code"],
                      "作废验收单：%s" % ((reason_text or "").strip() or "未填写原因"),
                      operator)
    return {"success": True, "message": "验收单 %s 已作废" % order["order_code"]}


def scan_overdue_accept(operator="admin"):
    """扫描超期未归档的验收单并标记超期，返回本次新标记条数。"""
    today = date_to_text(today_date())
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT order_code FROM md_accept_order WHERE accept_state IN "
            "('WAIT','DOING') AND overdue_flag=0 AND plan_date<>'' "
            "AND plan_date<?", (today,)).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE md_accept_order SET overdue_flag=1 WHERE order_code=?",
                (row["order_code"],))
        conn.commit()
    finally:
        conn.close()
    return len(rows)


# ------------------------------------------------------------------
# 分页查询
# ------------------------------------------------------------------
def fetch_accept_orders(keyword="", accept_type="", accept_state="",
                        overdue_only=False, page=1):
    """按条件分页查询验收单，返回分页结果与记录列表。"""
    sql = ("SELECT * FROM md_accept_order WHERE 1=1 ")
    args = []
    keyword = (keyword or "").strip()
    if keyword:
        sql += ("AND (order_code LIKE ? OR asset_code LIKE ? OR asset_name LIKE ? "
                "OR dept_name LIKE ? OR vendor_name LIKE ?) ")
        like = "%%%s%%" % keyword
        args.extend([like, like, like, like, like])
    if accept_type:
        sql += "AND accept_type=? "
        args.append(accept_type)
    if accept_state:
        sql += "AND accept_state=? "
        args.append(accept_state)
    if overdue_only:
        sql += "AND overdue_flag=1 "
    conn = get_connection()
    try:
        total = conn.execute(
            sql.replace("SELECT *", "SELECT COUNT(1) AS c", 1), args).fetchone()["c"]
        page_amount = max(1, (total + ORDER_PAGE_SIZE - 1) // ORDER_PAGE_SIZE)
        try:
            page_no = max(1, min(int(page), page_amount))
        except (TypeError, ValueError):
            page_no = 1
        rows = conn.execute(
            sql + "ORDER BY order_id DESC LIMIT ? OFFSET ?",
            args + [ORDER_PAGE_SIZE, (page_no - 1) * ORDER_PAGE_SIZE]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["type_label"] = ACCEPT_TYPE_DICT.get(item["accept_type"],
                                                    item["accept_type"])
        record["state_label"] = ACCEPT_STATE_DICT.get(item["accept_state"],
                                                      item["accept_state"])
        record["score"] = calc_accept_score(item["check_pass"], item["check_fail"])
        record["transfer_label"] = "已转资" if item["transfer_flag"] else "未转资"
        records.append(record)
    return {"records": records, "total": total, "page": page_no,
            "page_amount": page_amount}


def fetch_accept_checks(order_code="", item_result="", keyword="", limit=30):
    """按条件查询验收检查项，支持按验收单、结论与关键字过滤。"""
    sql = ("SELECT * FROM md_accept_check WHERE 1=1 ")
    args = []
    if order_code:
        sql += "AND order_code=? "
        args.append(order_code)
    if item_result:
        sql += "AND item_result=? "
        args.append(item_result)
    keyword = (keyword or "").strip()
    if keyword:
        sql += "AND (check_code LIKE ? OR item_name LIKE ? OR problem_desc LIKE ?) "
        like = "%%%s%%" % keyword
        args.extend([like, like, like])
    conn = get_connection()
    try:
        rows = conn.execute(
            sql + "ORDER BY check_id DESC LIMIT ?", args + [limit]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["result_label"] = CHECK_RESULT_DICT.get(item["item_result"],
                                                       item["item_result"])
        record["handle_label"] = "已整改" if item["handle_flag"] else "未整改"
        records.append(record)
    return records


# ------------------------------------------------------------------
# 统计与图表
# ------------------------------------------------------------------
def build_accept_summary():
    """汇总验收核心指标：单据量、通过量、不合格项、超期量、转资量与平均得分。"""
    scan_overdue_accept()
    conn = get_connection()
    try:
        order_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_accept_order").fetchone()["c"]
        state_rows = conn.execute(
            "SELECT accept_state, COUNT(1) AS c FROM md_accept_order "
            "GROUP BY accept_state").fetchall()
        overdue = conn.execute(
            "SELECT COUNT(1) AS c FROM md_accept_order WHERE overdue_flag=1"
        ).fetchone()["c"]
        transferred = conn.execute(
            "SELECT COUNT(1) AS c FROM md_accept_order WHERE transfer_flag=1"
        ).fetchone()["c"]
        transfer_amount = conn.execute(
            "SELECT COALESCE(SUM(accept_amount),0) AS s FROM md_accept_order "
            "WHERE transfer_flag=1").fetchone()["s"]
        item_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_accept_check").fetchone()["c"]
        fail_item = conn.execute(
            "SELECT COUNT(1) AS c FROM md_accept_check WHERE item_result='FAIL'"
        ).fetchone()["c"]
        rectified = conn.execute(
            "SELECT COUNT(1) AS c FROM md_accept_check WHERE handle_flag=1"
        ).fetchone()["c"]
        stage_rows = conn.execute(
            "SELECT stage_state, COUNT(1) AS c FROM md_accept_stage "
            "GROUP BY stage_state").fetchall()
        score_rows = conn.execute(
            "SELECT check_pass, check_fail FROM md_accept_order").fetchall()
    finally:
        conn.close()

    state_counter = {item["accept_state"]: item["c"] for item in state_rows}
    pass_amount = state_counter.get("PASS", 0)
    total_score = 0.0
    scored = 0
    for row in score_rows:
        value = calc_accept_score(row["check_pass"], row["check_fail"])
        if value > 0:
            total_score += value
            scored += 1
    stage_counter = {item["stage_state"]: item["c"] for item in stage_rows}
    return {
        "order_total": order_total,
        "pass_amount": pass_amount,
        "doing_amount": state_counter.get("DOING", 0),
        "wait_amount": state_counter.get("WAIT", 0),
        "reject_amount": state_counter.get("REJECT", 0),
        "overdue_amount": overdue,
        "transferred": transferred,
        "transfer_amount": round1(transfer_amount),
        "item_total": item_total,
        "fail_item": fail_item,
        "rectified": rectified,
        "done_stage": stage_counter.get("DONE", 0),
        "pass_rate": round1(pass_amount * 100.0 / order_total) if order_total else 0.0,
        "avg_score": round1(total_score / scored) if scored else 0.0,
    }


def build_accept_charts():
    """构建五类图表数据：验收类型分布、验收状态分布、检查结论分布、科室
    验收通过率对比与近 6 个月到货验收趋势。"""
    conn = get_connection()
    try:
        order_rows = conn.execute(
            "SELECT order_code, accept_type, accept_state, dept_name, "
            "arrive_date, accept_date, check_pass, check_fail FROM md_accept_order"
        ).fetchall()
        result_rows = conn.execute(
            "SELECT item_result, COUNT(1) AS c FROM md_accept_check "
            "GROUP BY item_result").fetchall()
    finally:
        conn.close()

    type_counter = {}
    for item in order_rows:
        type_counter[item["accept_type"]] = type_counter.get(item["accept_type"], 0) + 1
    type_distribution = [
        {"name": ACCEPT_TYPE_DICT.get(code, code), "value": amount}
        for code, amount in type_counter.items()
    ]

    state_counter = {}
    for item in order_rows:
        state_counter[item["accept_state"]] = state_counter.get(item["accept_state"], 0) + 1
    state_distribution = [
        {"name": ACCEPT_STATE_DICT.get(code, code), "value": state_counter.get(code, 0)}
        for code in ("WAIT", "DOING", "PASS", "REJECT", "VOID")
    ]

    result_counter = {item["item_result"]: item["c"] for item in result_rows}
    check_distribution = [
        {"name": CHECK_RESULT_DICT.get(code, code),
         "value": result_counter.get(code, 0)}
        for code in ("PASS", "FAIL", "NOTDO")
    ]

    dept_total = {}
    dept_pass = {}
    for item in order_rows:
        dept = (item["dept_name"] or "").strip() or "未指定科室"
        dept_total[dept] = dept_total.get(dept, 0) + 1
        if item["accept_state"] == "PASS":
            dept_pass[dept] = dept_pass.get(dept, 0) + 1
    dept_rate = []
    for dept, total in dept_total.items():
        dept_rate.append({
            "name": dept,
            "value": round1(dept_pass.get(dept, 0) * 100.0 / total) if total else 0.0,
            "total": total,
        })
    dept_rate.sort(key=lambda x: x["value"], reverse=True)

    month_keys = []
    cursor = today_date().replace(day=1)
    for _ in range(6):
        month_keys.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - datetime.timedelta(days=1)).replace(day=1)
    month_keys.reverse()
    arrive_counter = {key: 0 for key in month_keys}
    pass_counter = {key: 0 for key in month_keys}
    for item in order_rows:
        arrive_key = parse_date_text(item["arrive_date"])
        if arrive_key is not None and arrive_key.strftime("%Y-%m") in arrive_counter:
            arrive_counter[arrive_key.strftime("%Y-%m")] += 1
        if item["accept_state"] == "PASS":
            end_key = parse_date_text(item["accept_date"])
            if end_key is not None and end_key.strftime("%Y-%m") in pass_counter:
                pass_counter[end_key.strftime("%Y-%m")] += 1

    return {
        "type_distribution": type_distribution,
        "state_distribution": state_distribution,
        "check_distribution": check_distribution,
        "dept_rate": dept_rate[:8],
        "month_trend": {
            "months": month_keys,
            "arrive_values": [arrive_counter[key] for key in month_keys],
            "pass_values": [pass_counter[key] for key in month_keys],
        },
    }


def fetch_accept_options():
    """验收页面下拉选项：各类字典项、设备候选、科室候选与合同候选。"""
    conn = get_connection()
    try:
        dept_rows = conn.execute(
            "SELECT DISTINCT dept_name FROM md_asset_ledger WHERE dept_name<>'' "
            "ORDER BY dept_name").fetchall()
        order_rows = conn.execute(
            "SELECT order_code, asset_name FROM md_accept_order "
            "ORDER BY order_id DESC LIMIT 20").fetchall()
    finally:
        conn.close()
    return {
        "device_list": fetch_device_options(),
        "dept_list": [row["dept_name"] for row in dept_rows],
        "owner_list": list(ACCEPT_OWNER_LIST),
        "contract_list": fetch_contract_options(),
        "order_list": [dict(row) for row in order_rows],
        "type_options": [{"code": k, "label": v} for k, v in ACCEPT_TYPE_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in ACCEPT_STATE_DICT.items()],
        "result_options": [{"code": k, "label": v} for k, v in CHECK_RESULT_DICT.items()],
        "stage_options": [{"code": k, "label": v} for k, v in STAGE_STATE_DICT.items()],
    }


def fetch_recent_stages(order_code="", limit=40):
    """按条件查询装机阶段流转记录，支持按验收单过滤并取最近若干条。"""
    sql = ("SELECT * FROM md_accept_stage WHERE 1=1 ")
    args = []
    if order_code:
        sql += "AND order_code=? "
        args.append(order_code)
    conn = get_connection()
    try:
        rows = conn.execute(
            sql + "ORDER BY stage_id DESC LIMIT ?", args + [limit]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["state_label"] = STAGE_STATE_DICT.get(item["stage_state"],
                                                     item["stage_state"])
        records.append(record)
    return records


def build_accept_payload(keyword="", accept_type="", accept_state="",
                         overdue_only=False, check_order="", check_result="",
                         check_keyword="", page=1):
    """组装设备验收与装机转资管理页面所需的全部数据。"""
    pager = fetch_accept_orders(keyword, accept_type, accept_state,
                                overdue_only, page)
    return {
        "summary": build_accept_summary(),
        "charts": build_accept_charts(),
        "orders": pager["records"],
        "order_total": pager["total"],
        "page": pager["page"],
        "page_amount": pager["page_amount"],
        "checks": fetch_accept_checks(check_order, check_result, check_keyword),
        "stages": fetch_recent_stages(check_order),
        "options": fetch_accept_options(),
        "result": "OK",
    }


def build_accept_brief():
    """设备验收看板摘要，供运维总览看板嵌入。"""
    summary = build_accept_summary()
    return {
        "order_total": summary["order_total"],
        "pass_amount": summary["pass_amount"],
        "overdue_amount": summary["overdue_amount"],
        "fail_item": summary["fail_item"],
        "transferred": summary["transferred"],
        "pass_rate": summary["pass_rate"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_accept():
    """灌入设备验收演示数据：登记验收单、逐项检查、整改复检、阶段推进、
    验收归档与转资入账。

    :return: 生成的验收单条数
    """
    conn = get_connection()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM md_accept_order").fetchone()["c"]
        if already:
            return 0
    finally:
        conn.close()

    device_list = fetch_device_options(limit=60)
    if not device_list:
        return 0
    today = today_date()
    amount = 0

    for index, (accept_type, device_index, arrive_offset, cycle_days,
                target_state, remark) in enumerate(DEMO_ACCEPT_LIST):
        if device_index >= len(device_list):
            continue
        device = device_list[device_index]
        arrive_day = date_to_text(today + datetime.timedelta(days=arrive_offset))
        outcome = save_accept_order(
            "", device["asset_code"], accept_type,
            DEMO_VENDOR_LIST[index % len(DEMO_VENDOR_LIST)], "",
            "SN%s%03d" % (device["asset_code"][-4:], index + 1),
            arrive_day,
            date_to_text(today + datetime.timedelta(days=arrive_offset + cycle_days)),
            float(device["purchase_price"] or 0), 12,
            ACCEPT_OWNER_LIST[index % len(ACCEPT_OWNER_LIST)], remark,
            "医工科-郑工")
        if not outcome.get("success"):
            continue
        order_code = outcome["order_code"]
        amount += 1

        if target_state == "WAIT":
            continue

        checks = fetch_order_checks(order_code)
        # 第 3 张与第 6 张验收单各留一项不合格，用于演示整改复检与验收不通过
        make_fail = device_index in (2, 5)
        for item_index, item in enumerate(checks):
            if target_state == "DOING" and item_index >= 3:
                continue
            if make_fail and item_index == 3:
                save_check_result(
                    item["check_code"], "实测偏差超出允许范围", "FAIL",
                    "技术性能实测值超出技术标书允许偏差，需供应商整改后复检",
                    "医工科-郑工", "医工科-郑工")
            else:
                save_check_result(item["check_code"], "符合验收标准", "PASS",
                                  "", "医工科-郑工", "医工科-郑工")

        # 通过的单据走完全部阶段，验收中走两个阶段，不通过的单据止步于调试阶段
        if target_state == "PASS":
            stage_times = len(DEFAULT_STAGE_LIST)
        elif target_state == "DOING":
            stage_times = 2
        else:
            stage_times = 3
        for step in range(stage_times):
            advance_accept_stage(
                order_code, ACCEPT_OWNER_LIST[index % len(ACCEPT_OWNER_LIST)],
                "第 %d 个装机阶段已按计划完成" % (step + 1),
                date_to_text(today + datetime.timedelta(
                    days=arrive_offset + step * 2)), "医工科-郑工")

        if target_state == "REJECT":
            finish_accept_order(order_code, "REJECT",
                                date_to_text(today + datetime.timedelta(
                                    days=arrive_offset + cycle_days)),
                                0, "配件缺失未整改完成，判定验收不通过",
                                "医工科-郑工")
            continue
        if target_state != "PASS":
            continue

        # 整改复检：把不合格项改判合格后归档通过，再执行转资入账
        conn = get_connection()
        try:
            fail_rows = conn.execute(
                "SELECT check_code FROM md_accept_check WHERE order_code=? "
                "AND item_result='FAIL'", (order_code,)).fetchall()
        finally:
            conn.close()
        for row in fail_rows:
            rectify_check_item(row["check_code"],
                               "供应商已完成整改并复检合格，附整改报告",
                               "整改后复检合格", "医工科-郑工", "医工科-郑工")
        finish_accept_order(order_code, "PASS",
                            date_to_text(today + datetime.timedelta(
                                days=arrive_offset + cycle_days)),
                            0, "各项检查合格，同意验收通过并转资入账",
                            "医工科-郑工")
        transfer_accept_asset(order_code,
                              date_to_text(today + datetime.timedelta(
                                  days=arrive_offset + cycle_days)),
                              "医工科-郑工")

    # 作废一张待验收单据，用于演示作废状态与状态完整取值
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT order_code FROM md_accept_order WHERE accept_state='WAIT' "
            "ORDER BY order_id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    if row is not None:
        cancel_accept_order(row["order_code"], "供货合同终止，本验收单作废",
                            "医工科-郑工")
    return amount

# --------------------------------------------------------------------------
# 第 13 天数据表脚本增量：mdm_schema.sql（M，已内联为字符串常量 DAY13_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY13_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 13 天新增：设备验收与装机转资三表
-- ==================================================================
-- 设备验收单表：按「一台设备一次验收一张单」记录采购到货后的开箱清点、
-- 安装调试、技术性能检测与转资交付全过程，支撑验收进度跟踪与超期预警
CREATE TABLE IF NOT EXISTS md_accept_order (
    order_id        INTEGER PRIMARY KEY AUTOINCREMENT, -- 验收单主键
    order_code      TEXT    NOT NULL UNIQUE,           -- 验收单编号（AC-序号）
    asset_code      TEXT    NOT NULL,                  -- 关联设备资产编号
    asset_name      TEXT    DEFAULT '',                -- 设备名称快照
    dept_name       TEXT    DEFAULT '',                -- 使用科室快照
    vendor_name     TEXT    DEFAULT '',                -- 供货单位快照
    contract_code   TEXT    DEFAULT '',                -- 关联采购合同编号
    accept_type     TEXT    DEFAULT 'ARRIVE',          -- 验收类型（见 ACCEPT_TYPE_DICT）
    serial_no       TEXT    DEFAULT '',                -- 机身序列号
    arrive_date     TEXT    DEFAULT '',                -- 到货日期
    plan_date       TEXT    DEFAULT '',                -- 计划完成验收日期
    accept_date     TEXT    DEFAULT '',                -- 实际验收完成日期
    accept_amount   REAL    DEFAULT 0,                 -- 验收确认金额（元）
    warranty_months INTEGER DEFAULT 12,                -- 质保月数
    warranty_end    TEXT    DEFAULT '',                -- 质保到期日期
    accept_state    TEXT    DEFAULT 'WAIT',            -- 验收状态（见 ACCEPT_STATE_DICT）
    stage_name      TEXT    DEFAULT '',                -- 当前所处装机阶段
    check_total     INTEGER DEFAULT 0,                 -- 验收检查项目总数
    check_pass      INTEGER DEFAULT 0,                 -- 合格项目数
    check_fail      INTEGER DEFAULT 0,                 -- 不合格项目数
    transfer_flag   INTEGER DEFAULT 0,                 -- 是否已完成转资入账
    transfer_date   TEXT    DEFAULT '',                -- 转资入账日期
    acceptor_name   TEXT    DEFAULT '',                -- 验收负责人
    overdue_flag    INTEGER DEFAULT 0,                 -- 是否超期未完成验收
    remark_text     TEXT    DEFAULT '',                -- 验收说明
    operator_name   TEXT    DEFAULT '',                -- 登记人
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 验收检查项表：一张验收单展开的逐项检查明细，支撑结论登记与整改复检
CREATE TABLE IF NOT EXISTS md_accept_check (
    check_id        INTEGER PRIMARY KEY AUTOINCREMENT, -- 检查项主键
    check_code      TEXT    NOT NULL UNIQUE,           -- 检查项编号（ACK-序号）
    order_code      TEXT    DEFAULT '',                -- 所属验收单编号
    item_name       TEXT    DEFAULT '',                -- 检查项目名称
    check_method    TEXT    DEFAULT '',                -- 检查方法
    std_value       TEXT    DEFAULT '',                -- 合格标准
    check_value     TEXT    DEFAULT '',                -- 实测情况
    item_result     TEXT    DEFAULT 'NOTDO',           -- 检查结论（见 CHECK_RESULT_DICT）
    problem_desc    TEXT    DEFAULT '',                -- 不合格问题描述
    rectify_note    TEXT    DEFAULT '',                -- 整改复检说明
    handle_flag     INTEGER DEFAULT 0,                 -- 是否已整改复检合格
    checker_name    TEXT    DEFAULT '',                -- 检查人
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 装机阶段流转表：记录到货开箱到转资入账各阶段的计划与实际完成情况
CREATE TABLE IF NOT EXISTS md_accept_stage (
    stage_id        INTEGER PRIMARY KEY AUTOINCREMENT, -- 阶段主键
    stage_code      TEXT    NOT NULL UNIQUE,           -- 阶段编号（AS-序号）
    order_code      TEXT    DEFAULT '',                -- 所属验收单编号
    stage_name      TEXT    DEFAULT '',                -- 阶段名称
    stage_seq       INTEGER DEFAULT 0,                 -- 阶段顺序号
    plan_date       TEXT    DEFAULT '',                -- 计划完成日期
    finish_date     TEXT    DEFAULT '',                -- 实际完成日期
    executor_name   TEXT    DEFAULT '',                -- 阶段执行人
    stage_state     TEXT    DEFAULT 'WAIT',            -- 阶段状态（见 STAGE_STATE_DICT）
    duration_days   INTEGER DEFAULT 0,                 -- 本阶段实际耗时（天）
    remark_text     TEXT    DEFAULT '',                -- 阶段说明
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_aorder_code       ON md_accept_order (order_code);
CREATE INDEX IF NOT EXISTS idx_aorder_asset      ON md_accept_order (asset_code);
CREATE INDEX IF NOT EXISTS idx_aorder_type       ON md_accept_order (accept_type);
CREATE INDEX IF NOT EXISTS idx_aorder_state      ON md_accept_order (accept_state);
CREATE INDEX IF NOT EXISTS idx_aorder_date       ON md_accept_order (arrive_date);
CREATE INDEX IF NOT EXISTS idx_acheck_code       ON md_accept_check (check_code);
CREATE INDEX IF NOT EXISTS idx_acheck_order      ON md_accept_check (order_code);
CREATE INDEX IF NOT EXISTS idx_acheck_result     ON md_accept_check (item_result);
CREATE INDEX IF NOT EXISTS idx_astage_code       ON md_accept_stage (stage_code);
CREATE INDEX IF NOT EXISTS idx_astage_order      ON md_accept_stage (order_code);
CREATE INDEX IF NOT EXISTS idx_astage_state      ON md_accept_stage (stage_state);
"""


def apply_day13_schema(conn):
    """把第 13 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY13_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 13 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

from mdm_accept import (
    build_accept_payload,
    build_accept_brief,
    save_accept_order,
    save_check_result,
    rectify_check_item,
    advance_accept_stage,
    finish_accept_order,
    transfer_accept_asset,
    cancel_accept_order,
    scan_overdue_accept,
    seed_demo_accept,
    ACCEPT_TYPE_DICT,
    ACCEPT_STATE_DICT,
    CHECK_RESULT_DICT,
    STAGE_STATE_DICT,
)

# 【函数内插入】def api_overview():
#    # 第 13 天新增：总览叠加设备验收摘要，供验收概况卡渲染
#    payload["accept_brief"] = build_accept_brief()

@app.route("/md/accept")
def panel_accept():
    """设备验收与装机转资管理页面：验收登记、逐项检查、整改复检与转资入账（第 13 天）。"""
    write_ops_journal("设备验收", "查询", "PANEL-ACCEPT", "打开设备验收与装机转资管理页面")
    return render_template(
        "panel_accept.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/md/accept/panel")
def api_accept_panel():
    """验收面板数据接口：概览指标、五类图表、验收单、检查项与阶段流转。"""
    payload = build_accept_payload(
        keyword=request.args.get("keyword", ""),
        accept_type=request.args.get("accept_type", ""),
        accept_state=request.args.get("accept_state", ""),
        overdue_only=request.args.get("overdue_only", "0") in ("1", "true", "True"),
        check_order=request.args.get("check_order", ""),
        check_result=request.args.get("check_result", ""),
        check_keyword=request.args.get("check_keyword", ""),
        page=request.args.get("page", "1"),
    )
    return jsonify(payload)


@app.route("/api/md/accept/order/save", methods=["POST"])
def api_accept_order_save():
    """验收单登记接口：新建或编辑到货验收单并展开检查项与装机阶段。"""
    body = request.get_json(silent=True) or {}
    result = save_accept_order(
        body.get("order_code") or "",
        body.get("asset_code") or "",
        body.get("accept_type") or "ARRIVE",
        body.get("vendor_name") or "",
        body.get("contract_code") or "",
        body.get("serial_no") or "",
        body.get("arrive_date") or "",
        body.get("plan_date") or "",
        body.get("accept_amount") or 0,
        body.get("warranty_months") or 12,
        body.get("acceptor_name") or "",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/accept/check/save", methods=["POST"])
def api_accept_check_save():
    """验收检查结论登记接口：登记单项结论并回算验收单统计。"""
    body = request.get_json(silent=True) or {}
    result = save_check_result(
        body.get("check_code") or "",
        body.get("check_value") or "",
        body.get("item_result") or "PASS",
        body.get("problem_desc") or "",
        body.get("checker_name") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/accept/check/rectify", methods=["POST"])
def api_accept_check_rectify():
    """不合格项整改复检接口：填写整改说明并把结论改判为合格。"""
    body = request.get_json(silent=True) or {}
    result = rectify_check_item(
        body.get("check_code") or "",
        body.get("rectify_note") or "",
        body.get("check_value") or "",
        body.get("checker_name") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/accept/stage/advance", methods=["POST"])
def api_accept_stage_advance():
    """装机阶段推进接口：完成当前阶段并开启下一阶段。"""
    body = request.get_json(silent=True) or {}
    result = advance_accept_stage(
        body.get("order_code") or "",
        body.get("executor_name") or "",
        body.get("remark_text") or "",
        body.get("finish_date") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/accept/order/finish", methods=["POST"])
def api_accept_order_finish():
    """验收结论归档接口：存在未整改不合格项时不允许判为通过。"""
    body = request.get_json(silent=True) or {}
    result = finish_accept_order(
        body.get("order_code") or "",
        body.get("result_code") or "PASS",
        body.get("accept_date") or "",
        body.get("accept_amount") or 0,
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/accept/order/transfer", methods=["POST"])
def api_accept_order_transfer():
    """转资入账接口：回写台账采购原值与在用状态并登记状态流转。"""
    body = request.get_json(silent=True) or {}
    result = transfer_accept_asset(
        body.get("order_code") or "",
        body.get("transfer_date") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/accept/order/cancel", methods=["POST"])
def api_accept_order_cancel():
    """验收单作废接口：已验收通过的单据不可作废。"""
    body = request.get_json(silent=True) or {}
    result = cancel_accept_order(
        body.get("order_code") or "",
        body.get("reason_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/accept/scan", methods=["POST"])
def api_accept_scan():
    """超期验收扫描接口：标记计划完成日期早于今天且未归档的验收单。"""
    body = request.get_json(silent=True) or {}
    return jsonify({
        "result": "OK",
        "amount": scan_overdue_accept((body.get("operator") or "admin").strip()),
    })


@app.route("/api/md/accept/options")
def api_accept_options():
    """验收下拉选项接口：验收类型、验收状态、检查结论与阶段状态字典。"""
    return jsonify({
        "result": "OK",
        "type_options": [{"code": k, "label": v} for k, v in ACCEPT_TYPE_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in ACCEPT_STATE_DICT.items()],
        "result_options": [{"code": k, "label": v} for k, v in CHECK_RESULT_DICT.items()],
        "stage_options": [{"code": k, "label": v} for k, v in STAGE_STATE_DICT.items()],
    })



# 【函数内插入】def bootstrap_application():
#    seed_demo_accept()

# 【函数内插入】if __name__ == "__main__":
#    print("设备验收： http://127.0.0.1:%d/md/accept" % PORT)

# --------------------------------------------------------------------------
# 第 13 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/mdm_accept.js             新增 577 行
#   [修改] templates/layout_mdm.html        新增 1 行
#   [新增] templates/panel_accept.html      新增 459 行
