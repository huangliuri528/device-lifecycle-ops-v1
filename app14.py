# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-11-03（第 14 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 14 天新增与修改的源代码，不含第 1~13 天已有代码
#           本次迭代主题：新增设备调配与借用管理模块（申请登记、审批、出库回写台账、归还验收、损坏转维修、超期催还）
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5001/md/overview）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 14 天新增业务模块 1：mdm_allocate.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-11-03（第 14 天 / 共 15 天）
# 模块功能：设备调配与借用管理模块。实现跨科室调配申请单登记（调配类型、调出
#           调入科室、申请事由、申请人与预计归还日期）、一台申请单批量展开多台
#           设备明细、申请审批、出库执行回写设备台账归属科室与责任人并登记状态
#           流转、逐台归还验收（完好/磨损/损坏）、损坏设备一键联动生成维修工单、
#           整单归还归档、单据作废与借用超期催还扫描全过程管理；输出调配类型
#           分布、单据状态分布、归还完好状况分布、调出科室调配量对比与近 6 个月
#           调配申请趋势五类统计图表，形成「申请登记 → 审批 → 出库回写台账 →
#           逐台归还验收 → 损坏转维修 → 归档/超期催还」的设备流转闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from mdm_dal import get_connection, write_ops_journal, append_status_flow

# ------------------------------------------------------------------
# 调配类型字典：按院内设备流转的四种业务场景划分
# ------------------------------------------------------------------
ALLOCATE_TYPE_DICT = {
    "LOAN": "科室临时借用",
    "TRANSFER": "院内调拨",
    "EMERGENCY": "应急调配",
    "RESEARCH": "科研共享",
}

# 申请单状态字典：随审批、出库、归还逐级流转
ORDER_STATE_DICT = {
    "WAIT": "待审批",
    "APPROVED": "已批准待出库",
    "OUT": "已出库在用",
    "PARTIAL": "部分归还",
    "DONE": "已归还归档",
    "REJECT": "审批驳回",
    "VOID": "已作废",
}

# 设备明细状态字典
ITEM_STATE_DICT = {
    "WAIT": "待出库",
    "OUT": "已出库",
    "BACK": "已归还",
    "LOSS": "归还异常",
}

# 归还完好状况字典
BACK_CONDITION_DICT = {
    "GOOD": "完好",
    "WEAR": "轻微磨损",
    "DAMAGE": "损坏待修",
}

# 需要催还的借用类调配类型
LOAN_TYPE_LIST = ("LOAN", "EMERGENCY", "RESEARCH")

# 未闭环状态集合：超期扫描只处理这些状态的申请单
OPEN_STATE_LIST = ("WAIT", "APPROVED", "OUT", "PARTIAL")

# 调配申请人候选，供建单选择与演示数据灌入
APPLICANT_LIST = ("设备科-沈工", "医工科-郑工", "护理部-韩主任", "急诊科-曹主任")

# 审批人候选
APPROVER_LIST = ("设备处-卢处长", "院办-邱主任", "医工科-冯主任")

# 目标科室候选（与台账科室保持一致的补充值）
TARGET_DEPT_LIST = ("重症医学科", "急诊医学科", "影像诊断科", "手术麻醉科",
                    "检验医学中心", "康复医学科")

# 分页尺寸：申请单独立分页
ORDER_PAGE_SIZE = 10

# 演示申请单清单
# （调配类型，主设备序号，附加设备序号，目标科室，申请偏移天数，借用天数，
#  目标状态，申请事由）
DEMO_ALLOCATE_LIST = (
    ("LOAN", 0, 1, "重症医学科", -96, 12, "DONE", "重症病区短期增配监护设备"),
    ("TRANSFER", 2, 3, "影像诊断科", -72, 0, "DONE", "影像设备院内调拨至新诊区"),
    ("EMERGENCY", 4, 5, "急诊医学科", -38, 7, "PARTIAL", "急诊抢救设备应急调配"),
    ("RESEARCH", 6, 7, "康复医学科", -16, 20, "OUT", "康复设备科研项目共享使用"),
    ("LOAN", 0, 2, "手术麻醉科", -6, 10, "APPROVED", "手术量增加临时借用设备"),
    ("TRANSFER", 1, 3, "检验医学中心", 2, 0, "WAIT", "检验设备计划内调拨"),
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
    """读取业务表的自增主键最大值，用于生成不重复的流水编号。

    表名形如 md_allocate_xxx，去掉前缀后拼接 _id 即为该表主键列名。
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(%s_id),0) AS m FROM %s" % (
                table_name.replace("md_allocate_", ""), table_name)).fetchone()
    finally:
        conn.close()
    return int(row["m"] or 0) + 1


def build_order_code(seq_no):
    """生成调配申请单编号，形如 AL-0000007。"""
    return "AL-%07d" % seq_no


def build_item_code(seq_no):
    """生成调配设备明细编号，形如 ALI-0000031。"""
    return "ALI-%07d" % seq_no


def build_flow_code(seq_no):
    """生成调配流转编号，形如 ALF-0000019。"""
    return "ALF-%07d" % seq_no


# ------------------------------------------------------------------
# 查询辅助
# ------------------------------------------------------------------
def fetch_device_options(limit=80, dept_name=""):
    """读取台账设备候选，供调配建单选择待调配设备使用。"""
    dept_name = (dept_name or "").strip()
    sql = ("SELECT asset_code, asset_name, asset_model, dept_name, "
           "location_text, custodian, purchase_price, current_status "
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
    """按申请单编号读取一条调配申请单，不存在返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_allocate_order WHERE order_code=?",
            ((order_code or "").strip(),)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def fetch_order_items(order_code):
    """读取某张申请单下的全部设备明细，按明细编号升序返回。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM md_allocate_item WHERE order_code=? "
            "ORDER BY item_id ASC", ((order_code or "").strip(),)).fetchall()
    finally:
        conn.close()
    return [dict(item) for item in rows]


def write_flow(order_code, action_name, from_state, to_state, action_desc,
               operator, conn):
    """在给定连接的事务内写入一条调配流转留痕，调用方负责提交。

    流水序号直接在同一事务连接内读取最大值，避免未提交事务内重复取号撞号。
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(flow_id),0) AS m FROM md_allocate_flow").fetchone()
    seq = int(row["m"] or 0) + 1
    conn.execute(
        "INSERT INTO md_allocate_flow (flow_code, order_code, action_name, "
        "from_state, to_state, action_desc, operator_name, action_time) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (build_flow_code(seq), order_code, action_name, from_state, to_state,
         action_desc, (operator or "admin").strip(), now_text()))


def recalc_order_stat(order_code, conn=None):
    """重算某申请单的设备台数、已归还台数与异常台数，并推进单据状态。

    可在调用方的连接事务内执行（传入 conn），也可独立执行。
    """
    own_conn = conn is None
    db = conn if conn is not None else get_connection()
    try:
        stat = db.execute(
            "SELECT COUNT(1) AS total_c, "
            "SUM(CASE WHEN item_state='BACK' THEN 1 ELSE 0 END) AS back_c, "
            "SUM(CASE WHEN item_state='LOSS' THEN 1 ELSE 0 END) AS loss_c, "
            "SUM(CASE WHEN item_state='OUT' THEN 1 ELSE 0 END) AS out_c "
            "FROM md_allocate_item WHERE order_code=?", (order_code,)).fetchone()
        total = int(stat["total_c"] or 0)
        back = int(stat["back_c"] or 0)
        loss = int(stat["loss_c"] or 0)
        out = int(stat["out_c"] or 0)
        order = db.execute(
            "SELECT order_state FROM md_allocate_order WHERE order_code=?",
            (order_code,)).fetchone()
        new_state = order["order_state"] if order else "WAIT"
        if order is not None and order["order_state"] in ("OUT", "PARTIAL"):
            if back + loss >= total and total > 0:
                new_state = "PARTIAL"
            elif back > 0:
                new_state = "PARTIAL"
            elif out > 0:
                new_state = "OUT"
        db.execute(
            "UPDATE md_allocate_order SET item_total=?, item_back=?, "
            "item_loss=?, order_state=? WHERE order_code=?",
            (total, back, loss, new_state, order_code))
        if own_conn:
            db.commit()
    finally:
        if own_conn:
            db.close()
    return {"total": total, "back": back, "loss": loss, "out": out,
            "order_state": new_state}


# ------------------------------------------------------------------
# 申请单登记
# ------------------------------------------------------------------
def save_allocate_order(order_code, apply_title, allocate_type="LOAN",
                        to_dept="", asset_codes="", apply_reason="",
                        applicant_name="", apply_date="", due_date="",
                        remark_text="", operator="admin"):
    """新建或编辑一张设备调配申请单，新建时批量展开设备明细。

    asset_codes 为逗号分隔的设备资产编号串；新建时逐台从台账带出名称、
    型号、原值、原使用科室快照并落库；已出库或已归档的申请单不可再编辑。

    :return: 含 success、message、order_code 与 amount 的执行结果字典
    """
    apply_title = (apply_title or "").strip()
    if not apply_title:
        return {"success": False, "message": "请填写调配事由标题"}
    if allocate_type not in ALLOCATE_TYPE_DICT:
        return {"success": False, "message": "调配类型 %s 不合法" % allocate_type}
    to_dept = (to_dept or "").strip()
    if not to_dept:
        return {"success": False, "message": "请选择调入科室"}
    code_list = [item.strip() for item in (asset_codes or "").split(",")
                 if item.strip()]

    if (order_code or "").strip():
        exist = fetch_order_row(order_code)
        if exist is not None and exist["order_state"] not in ("WAIT", "REJECT"):
            return {"success": False, "message": "已审批或已出库的申请单不可编辑"}
        if exist is not None:
            apply_day = parse_date_text(apply_date) or parse_date_text(
                exist["apply_date"]) or today_date()
            conn = get_connection()
            try:
                conn.execute(
                    "UPDATE md_allocate_order SET apply_title=?, allocate_type=?, "
                    "to_dept=?, apply_reason=?, applicant_name=?, apply_date=?, "
                    "due_date=?, remark_text=?, operator_name=? WHERE order_code=?",
                    (apply_title, allocate_type, to_dept,
                     (apply_reason or "").strip(), (applicant_name or "").strip(),
                     date_to_text(apply_day), (due_date or "").strip(),
                     (remark_text or "").strip(), (operator or "admin").strip(),
                     order_code.strip()))
                conn.commit()
            finally:
                conn.close()
            write_ops_journal("设备调配", "申请编辑", order_code.strip(),
                              "编辑调配申请单：%s" % apply_title, operator)
            return {"success": True, "message": "调配申请单 %s 已更新" %
                    order_code.strip(), "order_code": order_code.strip(),
                    "amount": 0}

    if not code_list:
        return {"success": False, "message": "请至少选择一台待调配设备"}

    apply_day = parse_date_text(apply_date) or today_date()
    apply_text = date_to_text(apply_day)
    due_text = (due_date or "").strip()
    if allocate_type in LOAN_TYPE_LIST and not due_text:
        due_text = date_to_text(apply_day + datetime.timedelta(days=14))

    conn = get_connection()
    try:
        asset_rows = {}
        for code in code_list:
            asset_rows[code] = conn.execute(
                "SELECT asset_code, asset_name, asset_model, dept_name, "
                "purchase_price FROM md_asset_ledger WHERE asset_code=?",
                (code,)).fetchone()
        missing = [code for code in code_list if asset_rows[code] is None]
        if missing:
            return {"success": False,
                    "message": "设备 %s 不存在于台账" % "、".join(missing[:3])}

        code = build_order_code(next_seq_no("md_allocate_order"))
        from_dept = asset_rows[code_list[0]]["dept_name"] or ""
        conn.execute(
            "INSERT INTO md_allocate_order (order_code, apply_title, "
            "allocate_type, from_dept, to_dept, apply_reason, applicant_name, "
            "apply_date, due_date, order_state, item_total, remark_text, "
            "operator_name) VALUES (?,?,?,?,?,?,?,?,?,'WAIT',?,?,?)",
            (code, apply_title, allocate_type, from_dept, to_dept,
             (apply_reason or "").strip(), (applicant_name or "").strip(),
             apply_text, due_text, len(code_list), (remark_text or "").strip(),
             (operator or "admin").strip()))

        item_seq = next_seq_no("md_allocate_item")
        for asset_code in code_list:
            asset = asset_rows[asset_code]
            conn.execute(
                "INSERT INTO md_allocate_item (item_code, order_code, "
                "asset_code, asset_name, asset_model, original_price, "
                "from_dept, to_dept, item_state) "
                "VALUES (?,?,?,?,?,?,?,?,'WAIT')",
                (build_item_code(item_seq), code, asset_code,
                 asset["asset_name"], asset["asset_model"] or "",
                 float(asset["purchase_price"] or 0),
                 asset["dept_name"] or "", to_dept))
            item_seq += 1
        write_flow(code, "申请登记", "", "WAIT",
                   "登记调配申请：%s，共 %d 台设备" % (apply_title, len(code_list)),
                   operator, conn)
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("设备调配", "申请登记", code,
                      "登记 %s 调配申请单，调入 %s，共 %d 台设备" % (
                          ALLOCATE_TYPE_DICT[allocate_type], to_dept,
                          len(code_list)), operator)
    return {"success": True,
            "message": "调配申请单 %s 已生成，展开 %d 台设备明细" %
                       (code, len(code_list)),
            "order_code": code, "amount": len(code_list)}


def add_allocate_item(order_code, asset_code, operator="admin"):
    """为一张待审批申请单追加一台设备明细。"""
    order = fetch_order_row(order_code)
    if order is None:
        return {"success": False, "message": "调配申请单不存在"}
    if order["order_state"] not in ("WAIT", "REJECT"):
        return {"success": False, "message": "仅待审批或驳回的单据可追加设备"}
    conn = get_connection()
    try:
        asset = conn.execute(
            "SELECT asset_code, asset_name, asset_model, dept_name, "
            "purchase_price FROM md_asset_ledger WHERE asset_code=?",
            ((asset_code or "").strip(),)).fetchone()
        if asset is None:
            return {"success": False, "message": "设备不存在于台账"}
        dup = conn.execute(
            "SELECT COUNT(1) AS c FROM md_allocate_item WHERE order_code=? "
            "AND asset_code=?", (order["order_code"], asset_code)).fetchone()["c"]
        if dup:
            return {"success": False, "message": "该设备已在申请单明细中"}
        seq = next_seq_no("md_allocate_item")
        conn.execute(
            "INSERT INTO md_allocate_item (item_code, order_code, asset_code, "
            "asset_name, asset_model, original_price, from_dept, to_dept, "
            "item_state) VALUES (?,?,?,?,?,?,?,?,'WAIT')",
            (build_item_code(seq), order["order_code"], asset["asset_code"],
             asset["asset_name"], asset["asset_model"] or "",
             float(asset["purchase_price"] or 0), asset["dept_name"] or "",
             order["to_dept"]))
        conn.commit()
    finally:
        conn.close()
    recalc_order_stat(order["order_code"])
    write_ops_journal("设备调配", "追加设备", order["order_code"],
                      "追加调配设备 %s" % asset["asset_name"], operator)
    return {"success": True, "message": "已追加设备 %s" % asset["asset_name"]}


def remove_allocate_item(item_code, operator="admin"):
    """删除一条尚未出库的调配设备明细。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_allocate_item WHERE item_code=?",
            ((item_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "调配明细不存在"}
        if row["item_state"] != "WAIT":
            return {"success": False, "message": "已出库的设备明细不可移除"}
        conn.execute("DELETE FROM md_allocate_item WHERE item_code=?",
                     (row["item_code"],))
        conn.commit()
    finally:
        conn.close()
    recalc_order_stat(row["order_code"])
    write_ops_journal("设备调配", "移除设备", row["order_code"],
                      "移除调配明细 %s" % item_code, operator)
    return {"success": True, "message": "调配明细 %s 已移除" % item_code}


# ------------------------------------------------------------------
# 审批与出库执行
# ------------------------------------------------------------------
def approve_allocate_order(order_code, result_code="APPROVE", approver_name="",
                           approve_date="", remark_text="", operator="admin"):
    """审批一张调配申请单：通过进入待出库，驳回则终止流转。

    :return: 含 success 与 message 的执行结果字典
    """
    if result_code not in ("APPROVE", "REJECT"):
        return {"success": False, "message": "审批结论 %s 不合法" % result_code}
    order = fetch_order_row(order_code)
    if order is None:
        return {"success": False, "message": "调配申请单不存在"}
    if order["order_state"] not in ("WAIT", "REJECT"):
        return {"success": False,
                "message": "仅待审批或驳回的单据可以审批，当前为%s" %
                           ORDER_STATE_DICT.get(order["order_state"],
                                               order["order_state"])}
    items = fetch_order_items(order["order_code"])
    if not items:
        return {"success": False, "message": "该申请单没有待调配设备"}

    end_text = date_to_text(parse_date_text(approve_date) or today_date())
    target_state = "APPROVED" if result_code == "APPROVE" else "REJECT"
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_allocate_order SET order_state=?, approver_name=?, "
            "approve_date=?, remark_text=? WHERE order_code=?",
            (target_state, (approver_name or "").strip(), end_text,
             (remark_text or "").strip() or order["remark_text"],
             order["order_code"]))
        write_flow(order["order_code"], "申请审批", order["order_state"],
                   target_state,
                   "审批%s：%s，%d 台设备" % (
                       "通过" if result_code == "APPROVE" else "驳回",
                       (remark_text or "").strip() or "无补充意见", len(items)),
                   (approver_name or operator or "admin"), conn)
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("设备调配", "申请审批", order["order_code"],
                      "审批结论 %s，审批人 %s" % (
                          ORDER_STATE_DICT[target_state],
                          (approver_name or "").strip()), operator)
    return {"success": True,
            "message": "调配申请单 %s 已%s" % (
                order["order_code"],
                "批准，等待出库" if result_code == "APPROVE" else "驳回")}


def execute_allocate_order(order_code, out_date="", operator="admin"):
    """出库执行：逐台回写设备台账归属科室、存放位置与责任人并登记状态流转。

    仅已批准的单据可执行；执行后明细状态改「已出库」，单据状态改「已出库在用」。

    :return: 含 success、message 与 amount 的执行结果字典
    """
    order = fetch_order_row(order_code)
    if order is None:
        return {"success": False, "message": "调配申请单不存在"}
    if order["order_state"] != "APPROVED":
        return {"success": False, "message": "仅已批准待出库的申请单可以执行出库"}

    out_text = date_to_text(parse_date_text(out_date) or today_date())
    items = fetch_order_items(order["order_code"])
    pending = [item for item in items if item["item_state"] == "WAIT"]
    if not pending:
        return {"success": False, "message": "该申请单没有待出库设备"}

    conn = get_connection()
    try:
        for item in pending:
            asset = conn.execute(
                "SELECT asset_id, current_status FROM md_asset_ledger "
                "WHERE asset_code=?", (item["asset_code"],)).fetchone()
            from_status = asset["current_status"] if asset else "IN_USE"
            conn.execute(
                "UPDATE md_asset_ledger SET dept_name=?, location_text=?, "
                "custodian=? WHERE asset_code=?",
                (order["to_dept"], "%s（调配在用）" % order["to_dept"],
                 (order["applicant_name"] or "").strip(), item["asset_code"]))
            if asset is not None:
                conn.execute(
                    "INSERT INTO md_status_flow (asset_id, from_status, "
                    "to_status, change_reason, operator_name) VALUES (?,?,?,?,?)",
                    (asset["asset_id"], from_status, from_status,
                     "调配出库：%s → %s，申请单 %s" % (
                         item["from_dept"] or "--", order["to_dept"],
                         order["order_code"]), (operator or "admin").strip()))
            conn.execute(
                "UPDATE md_allocate_item SET item_state='OUT', out_date=? "
                "WHERE item_code=?", (out_text, item["item_code"]))
        conn.execute(
            "UPDATE md_allocate_order SET order_state='OUT', out_date=? "
            "WHERE order_code=?", (out_text, order["order_code"]))
        write_flow(order["order_code"], "出库执行", "APPROVED", "OUT",
                   "完成 %d 台设备出库，归属科室回写为 %s" % (
                       len(pending), order["to_dept"]), operator, conn)
        conn.commit()
    finally:
        conn.close()
    recalc_order_stat(order["order_code"])
    write_ops_journal("设备调配", "出库执行", order["order_code"],
                      "出库 %d 台设备至 %s，已回写设备台账" % (
                          len(pending), order["to_dept"]), operator)
    return {"success": True,
            "message": "调配申请单 %s 已出库 %d 台设备，台账归属已更新" % (
                order["order_code"], len(pending)), "amount": len(pending)}


# ------------------------------------------------------------------
# 归还验收与损坏联动
# ------------------------------------------------------------------
def return_allocate_item(item_code, back_condition="GOOD", back_note="",
                         back_date="", operator="admin"):
    """逐台归还验收：登记完好状况，损坏设备自动联动生成维修工单。

    归还时把设备台账归属还原到原使用科室；状况为损坏时调用维修工单模块
    真实登记一张报修工单，并把明细标记为「归还异常」。

    :return: 含 success、message 与 repair_code 的执行结果字典
    """
    if back_condition not in BACK_CONDITION_DICT:
        return {"success": False, "message": "归还状况 %s 不合法" % back_condition}
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_allocate_item WHERE item_code=?",
            ((item_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "调配明细不存在"}
        if row["item_state"] != "OUT":
            return {"success": False, "message": "仅已出库设备可以归还验收"}
        order = conn.execute(
            "SELECT * FROM md_allocate_order WHERE order_code=?",
            (row["order_code"],)).fetchone()
        if order is None:
            return {"success": False, "message": "所属申请单不存在"}
    finally:
        conn.close()

    back_text = date_to_text(parse_date_text(back_date) or today_date())
    loss_flag = back_condition == "DAMAGE"
    repair_code = ""
    if loss_flag:
        try:
            from mdm_repair import create_repair_order

            outcome = create_repair_order(
                row["asset_code"], "MECH", "MID",
                "调配归还验收发现设备损坏：%s" % ((back_note or "").strip() or
                                            "外观或功能异常，需检修"),
                (order["applicant_name"] or "").strip() or "设备科-沈工",
                "", "", row["to_dept"], row["asset_name"],
                "来源：调配申请单 %s 归还验收" % row["order_code"], operator)
            if outcome.get("success"):
                repair_code = outcome.get("order_code", "")
        except Exception:
            repair_code = ""

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_allocate_item SET item_state=?, back_date=?, "
            "back_condition=?, back_note=? WHERE item_code=?",
            ("LOSS" if loss_flag else "BACK", back_text, back_condition,
             (back_note or "").strip(), row["item_code"]))
        conn.execute(
            "UPDATE md_asset_ledger SET dept_name=?, location_text=?, "
            "custodian=? WHERE asset_code=?",
            (row["from_dept"] or "", row["from_dept"] or "",
             "", row["asset_code"]))
        asset = conn.execute(
            "SELECT asset_id, current_status FROM md_asset_ledger "
            "WHERE asset_code=?", (row["asset_code"],)).fetchone()
        if asset is not None:
            conn.execute(
                "INSERT INTO md_status_flow (asset_id, from_status, "
                "to_status, change_reason, operator_name) VALUES (?,?,?,?,?)",
                (asset["asset_id"], asset["current_status"],
                 asset["current_status"],
                 "调配归还入库：%s → 原科室 %s，状况 %s" % (
                     row["to_dept"], row["from_dept"] or "--",
                     BACK_CONDITION_DICT[back_condition]),
                 (operator or "admin").strip()))
        write_flow(row["order_code"], "归还验收", "OUT",
                   "LOSS" if loss_flag else "BACK",
                   "设备 %s 归还验收：%s%s" % (
                       row["asset_name"], BACK_CONDITION_DICT[back_condition],
                       "，已联动维修工单 %s" % repair_code if repair_code else ""),
                   operator, conn)
        conn.commit()
    finally:
        conn.close()
    recalc_order_stat(row["order_code"])
    write_ops_journal("设备调配", "归还验收", row["order_code"],
                      "设备 %s 归还，状况 %s" % (
                          row["asset_name"], BACK_CONDITION_DICT[back_condition]),
                      operator)
    message = "设备 %s 已归还验收（%s）" % (
        row["asset_name"], BACK_CONDITION_DICT[back_condition])
    if repair_code:
        message += "，已联动维修工单 %s" % repair_code
    return {"success": True, "message": message, "repair_code": repair_code}


def close_allocate_order(order_code, back_date="", remark_text="",
                         operator="admin"):
    """整单归还归档：全部设备完成归还验收后方可归档。

    :return: 含 success 与 message 的执行结果字典
    """
    order = fetch_order_row(order_code)
    if order is None:
        return {"success": False, "message": "调配申请单不存在"}
    if order["order_state"] == "DONE":
        return {"success": False, "message": "该申请单已归档"}
    if order["order_state"] in ("WAIT", "REJECT", "VOID"):
        return {"success": False, "message": "未出库或已作废的单据不可归档"}

    items = fetch_order_items(order["order_code"])
    pending = [item for item in items
               if item["item_state"] in ("WAIT", "OUT")]
    if pending:
        return {"success": False,
                "message": "仍有 %d 台设备未完成归还验收，不可归档" % len(pending)}

    back_text = date_to_text(parse_date_text(back_date) or today_date())
    conditions = [item["back_condition"] for item in items
                  if item["back_condition"]]
    overall = "DAMAGE" if "DAMAGE" in conditions else (
        "WEAR" if "WEAR" in conditions else "GOOD")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_allocate_order SET order_state='DONE', back_date=?, "
            "back_condition=?, overdue_flag=0, remark_text=? WHERE order_code=?",
            (back_text, overall,
             (remark_text or "").strip() or order["remark_text"],
             order["order_code"]))
        write_flow(order["order_code"], "整单归档", order["order_state"],
                   "DONE", "全部 %d 台设备归还验收完成，整单归档" % len(items),
                   operator, conn)
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("设备调配", "整单归档", order["order_code"],
                      "归还归档 %d 台设备，整体状况 %s" % (
                          len(items), BACK_CONDITION_DICT[overall]), operator)
    return {"success": True,
            "message": "调配申请单 %s 已归还归档，共 %d 台设备" % (
                order["order_code"], len(items))}


def cancel_allocate_order(order_code, reason_text="", operator="admin"):
    """作废一张调配申请单：已出库或已归档的单据不可作废。"""
    order = fetch_order_row(order_code)
    if order is None:
        return {"success": False, "message": "调配申请单不存在"}
    if order["order_state"] in ("OUT", "PARTIAL", "DONE"):
        return {"success": False, "message": "已出库或已归档的申请单不可作废"}
    if order["order_state"] == "VOID":
        return {"success": False, "message": "该申请单已作废"}
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_allocate_order SET order_state='VOID', remark_text=? "
            "WHERE order_code=?",
            ((reason_text or "").strip(), order["order_code"]))
        write_flow(order["order_code"], "申请作废", order["order_state"],
                   "VOID", "作废调配申请：%s" %
                   ((reason_text or "").strip() or "未填写原因"), operator, conn)
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("设备调配", "申请作废", order["order_code"],
                      "作废调配申请单：%s" %
                      ((reason_text or "").strip() or "未填写原因"), operator)
    return {"success": True, "message": "调配申请单 %s 已作废" % order["order_code"]}


def scan_overdue_allocate(operator="admin"):
    """扫描借用类超期未归还的申请单并标记，返回本次新标记条数。"""
    today = date_to_text(today_date())
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT order_code FROM md_allocate_order "
            "WHERE allocate_type IN ('LOAN','EMERGENCY','RESEARCH') "
            "AND order_state IN ('APPROVED','OUT','PARTIAL') "
            "AND overdue_flag=0 AND due_date<>'' AND due_date<?",
            (today,)).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE md_allocate_order SET overdue_flag=1 WHERE order_code=?",
                (row["order_code"],))
        conn.commit()
    finally:
        conn.close()
    return len(rows)


# ------------------------------------------------------------------
# 分页查询
# ------------------------------------------------------------------
def fetch_allocate_orders(keyword="", allocate_type="", order_state="",
                          overdue_only=False, page=1):
    """按条件分页查询调配申请单，返回分页结果与记录列表。"""
    sql = "SELECT * FROM md_allocate_order WHERE 1=1 "
    args = []
    keyword = (keyword or "").strip()
    if keyword:
        sql += ("AND (order_code LIKE ? OR apply_title LIKE ? OR from_dept LIKE ? "
                "OR to_dept LIKE ? OR applicant_name LIKE ?) ")
        like = "%%%s%%" % keyword
        args.extend([like, like, like, like, like])
    if allocate_type:
        sql += "AND allocate_type=? "
        args.append(allocate_type)
    if order_state:
        sql += "AND order_state=? "
        args.append(order_state)
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
        record["type_label"] = ALLOCATE_TYPE_DICT.get(item["allocate_type"],
                                                      item["allocate_type"])
        record["state_label"] = ORDER_STATE_DICT.get(item["order_state"],
                                                     item["order_state"])
        record["condition_label"] = BACK_CONDITION_DICT.get(
            item["back_condition"], "") if item["back_condition"] else "--"
        record["back_rate"] = round1(
            item["item_back"] * 100.0 / item["item_total"]) \
            if item["item_total"] else 0.0
        records.append(record)
    return {"records": records, "total": total, "page": page_no,
            "page_amount": page_amount}


def fetch_allocate_items(order_code="", item_state="", keyword="", limit=40):
    """按条件查询调配设备明细，支持按申请单、明细状态与关键字过滤。"""
    sql = "SELECT * FROM md_allocate_item WHERE 1=1 "
    args = []
    if order_code:
        sql += "AND order_code=? "
        args.append(order_code)
    if item_state:
        sql += "AND item_state=? "
        args.append(item_state)
    keyword = (keyword or "").strip()
    if keyword:
        sql += ("AND (item_code LIKE ? OR asset_code LIKE ? OR asset_name LIKE ? "
                "OR to_dept LIKE ?) ")
        like = "%%%s%%" % keyword
        args.extend([like, like, like, like])
    conn = get_connection()
    try:
        rows = conn.execute(
            sql + "ORDER BY item_id DESC LIMIT ?", args + [limit]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        record = dict(item)
        record["state_label"] = ITEM_STATE_DICT.get(item["item_state"],
                                                    item["item_state"])
        record["condition_label"] = BACK_CONDITION_DICT.get(
            item["back_condition"], "") if item["back_condition"] else "--"
        records.append(record)
    return records


def fetch_allocate_flows(order_code="", limit=40):
    """按条件查询调配流转记录，取最近若干条。"""
    sql = "SELECT * FROM md_allocate_flow WHERE 1=1 "
    args = []
    if order_code:
        sql += "AND order_code=? "
        args.append(order_code)
    conn = get_connection()
    try:
        rows = conn.execute(
            sql + "ORDER BY flow_id DESC LIMIT ?", args + [limit]).fetchall()
    finally:
        conn.close()
    return [dict(item) for item in rows]


# ------------------------------------------------------------------
# 统计与图表
# ------------------------------------------------------------------
def build_allocate_summary():
    """汇总调配核心指标：单据量、在用量、超期量、归还量、损坏联动量与归档率。"""
    scan_overdue_allocate()
    conn = get_connection()
    try:
        order_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_allocate_order").fetchone()["c"]
        state_rows = conn.execute(
            "SELECT order_state, COUNT(1) AS c FROM md_allocate_order "
            "GROUP BY order_state").fetchall()
        overdue = conn.execute(
            "SELECT COUNT(1) AS c FROM md_allocate_order WHERE overdue_flag=1"
        ).fetchone()["c"]
        item_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_allocate_item").fetchone()["c"]
        back_item = conn.execute(
            "SELECT COUNT(1) AS c FROM md_allocate_item WHERE item_state='BACK'"
        ).fetchone()["c"]
        loss_item = conn.execute(
            "SELECT COUNT(1) AS c FROM md_allocate_item WHERE item_state='LOSS'"
        ).fetchone()["c"]
        out_item = conn.execute(
            "SELECT COUNT(1) AS c FROM md_allocate_item WHERE item_state='OUT'"
        ).fetchone()["c"]
        damage_item = conn.execute(
            "SELECT COUNT(1) AS c FROM md_allocate_item WHERE back_condition='DAMAGE'"
        ).fetchone()["c"]
        amount_row = conn.execute(
            "SELECT COALESCE(SUM(original_price),0) AS s FROM md_allocate_item"
        ).fetchone()
    finally:
        conn.close()
    state_counter = {item["order_state"]: item["c"] for item in state_rows}
    done_amount = state_counter.get("DONE", 0)
    return {
        "order_total": order_total,
        "wait_amount": state_counter.get("WAIT", 0),
        "approved_amount": state_counter.get("APPROVED", 0),
        "out_amount": state_counter.get("OUT", 0) + state_counter.get("PARTIAL", 0),
        "done_amount": done_amount,
        "reject_amount": state_counter.get("REJECT", 0),
        "overdue_amount": overdue,
        "item_total": item_total,
        "out_item": out_item,
        "back_item": back_item,
        "loss_item": loss_item,
        "damage_item": damage_item,
        "allocate_amount": round1(amount_row["s"]),
        "back_rate": round1((back_item + loss_item) * 100.0 / item_total)
        if item_total else 0.0,
        "close_rate": round1(done_amount * 100.0 / order_total)
        if order_total else 0.0,
    }


def build_allocate_charts():
    """构建五类图表数据：调配类型分布、单据状态分布、归还完好状况分布、
    调出科室调配设备量对比与近 6 个月调配申请趋势。"""
    conn = get_connection()
    try:
        order_rows = conn.execute(
            "SELECT order_code, allocate_type, order_state, from_dept, "
            "to_dept, apply_date, back_date FROM md_allocate_order").fetchall()
        condition_rows = conn.execute(
            "SELECT back_condition, COUNT(1) AS c FROM md_allocate_item "
            "WHERE back_condition<>'' GROUP BY back_condition").fetchall()
        dept_rows = conn.execute(
            "SELECT from_dept, COUNT(1) AS c FROM md_allocate_item "
            "GROUP BY from_dept").fetchall()
    finally:
        conn.close()

    type_counter = {}
    state_counter = {}
    for item in order_rows:
        type_counter[item["allocate_type"]] = type_counter.get(
            item["allocate_type"], 0) + 1
        state_counter[item["order_state"]] = state_counter.get(
            item["order_state"], 0) + 1
    type_distribution = [
        {"name": ALLOCATE_TYPE_DICT.get(code, code), "value": amount}
        for code, amount in type_counter.items()
    ]
    state_distribution = [
        {"name": ORDER_STATE_DICT.get(code, code), "value": state_counter.get(code, 0)}
        for code in ("WAIT", "APPROVED", "OUT", "PARTIAL", "DONE", "REJECT", "VOID")
    ]

    condition_counter = {item["back_condition"]: item["c"]
                         for item in condition_rows}
    condition_distribution = [
        {"name": BACK_CONDITION_DICT.get(code, code),
         "value": condition_counter.get(code, 0)}
        for code in ("GOOD", "WEAR", "DAMAGE")
    ]

    dept_rank = [
        {"name": (item["from_dept"] or "").strip() or "未指定科室",
         "value": int(item["c"] or 0)}
        for item in dept_rows
    ]
    dept_rank.sort(key=lambda x: x["value"], reverse=True)

    month_keys = []
    cursor = today_date().replace(day=1)
    for _ in range(6):
        month_keys.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - datetime.timedelta(days=1)).replace(day=1)
    month_keys.reverse()
    apply_counter = {key: 0 for key in month_keys}
    back_counter = {key: 0 for key in month_keys}
    for item in order_rows:
        apply_day = parse_date_text(item["apply_date"])
        if apply_day is not None and apply_day.strftime("%Y-%m") in apply_counter:
            apply_counter[apply_day.strftime("%Y-%m")] += 1
        back_day = parse_date_text(item["back_date"])
        if back_day is not None and back_day.strftime("%Y-%m") in back_counter:
            back_counter[back_day.strftime("%Y-%m")] += 1

    return {
        "type_distribution": type_distribution,
        "state_distribution": state_distribution,
        "condition_distribution": condition_distribution,
        "dept_rank": dept_rank[:8],
        "month_trend": {
            "months": month_keys,
            "apply_values": [apply_counter[key] for key in month_keys],
            "back_values": [back_counter[key] for key in month_keys],
        },
    }


def fetch_allocate_options():
    """调配页面下拉选项：各类字典项、设备候选、科室候选与申请单候选。"""
    conn = get_connection()
    try:
        dept_rows = conn.execute(
            "SELECT DISTINCT dept_name FROM md_asset_ledger WHERE dept_name<>'' "
            "ORDER BY dept_name").fetchall()
        order_rows = conn.execute(
            "SELECT order_code, apply_title FROM md_allocate_order "
            "ORDER BY order_id DESC LIMIT 20").fetchall()
    finally:
        conn.close()
    return {
        "device_list": fetch_device_options(),
        "dept_list": [row["dept_name"] for row in dept_rows],
        "target_list": list(TARGET_DEPT_LIST),
        "applicant_list": list(APPLICANT_LIST),
        "approver_list": list(APPROVER_LIST),
        "order_list": [dict(row) for row in order_rows],
        "type_options": [{"code": k, "label": v}
                         for k, v in ALLOCATE_TYPE_DICT.items()],
        "state_options": [{"code": k, "label": v}
                          for k, v in ORDER_STATE_DICT.items()],
        "item_state_options": [{"code": k, "label": v}
                               for k, v in ITEM_STATE_DICT.items()],
        "condition_options": [{"code": k, "label": v}
                              for k, v in BACK_CONDITION_DICT.items()],
    }


def build_allocate_payload(keyword="", allocate_type="", order_state="",
                           overdue_only=False, item_order="", item_state="",
                           item_keyword="", flow_order="", page=1):
    """组装设备调配与借用管理页面所需的全部数据。"""
    pager = fetch_allocate_orders(keyword, allocate_type, order_state,
                                  overdue_only, page)
    return {
        "summary": build_allocate_summary(),
        "charts": build_allocate_charts(),
        "orders": pager["records"],
        "order_total": pager["total"],
        "page": pager["page"],
        "page_amount": pager["page_amount"],
        "items": fetch_allocate_items(item_order, item_state, item_keyword),
        "flows": fetch_allocate_flows(flow_order),
        "options": fetch_allocate_options(),
        "result": "OK",
    }


def build_allocate_brief():
    """设备调配看板摘要，供运维总览看板嵌入。"""
    summary = build_allocate_summary()
    return {
        "order_total": summary["order_total"],
        "out_amount": summary["out_amount"],
        "overdue_amount": summary["overdue_amount"],
        "back_item": summary["back_item"],
        "damage_item": summary["damage_item"],
        "back_rate": summary["back_rate"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_allocate():
    """灌入设备调配演示数据：申请登记、审批、出库回写台账、逐台归还验收、
    损坏联动维修工单、整单归档、驳回与作废、超期催还扫描。

    :return: 生成的调配申请单条数
    """
    conn = get_connection()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM md_allocate_order").fetchone()["c"]
        if already:
            return 0
    finally:
        conn.close()

    device_list = fetch_device_options(limit=60)
    if not device_list:
        return 0
    today = today_date()
    amount = 0

    for index, (allocate_type, main_index, extra_index, to_dept,
                apply_offset, loan_days, target_state, reason) in enumerate(
            DEMO_ALLOCATE_LIST):
        codes = []
        for device_index in (main_index, extra_index):
            if device_index < len(device_list):
                codes.append(device_list[device_index]["asset_code"])
        if not codes:
            continue
        apply_day = date_to_text(today + datetime.timedelta(days=apply_offset))
        due_day = date_to_text(today + datetime.timedelta(
            days=apply_offset + loan_days)) if loan_days else ""
        outcome = save_allocate_order(
            "", reason, allocate_type, to_dept, ",".join(codes),
            "%s，需协调 %d 台设备跨科室流转并登记归还验收" % (
                reason, len(codes)),
            APPLICANT_LIST[index % len(APPLICANT_LIST)], apply_day, due_day,
            "演示数据：%s" % ALLOCATE_TYPE_DICT[allocate_type],
            "设备科-沈工")
        if not outcome.get("success"):
            continue
        order_code = outcome["order_code"]
        amount += 1

        if target_state == "WAIT":
            continue

        # 倒数第二张单演示审批驳回，倒数第一张演示作废
        if target_state == "REJECT":
            approve_allocate_order(
                order_code, "REJECT",
                APPROVER_LIST[index % len(APPROVER_LIST)], apply_day,
                "设备台账在用冲突，暂不批准调配", "设备科-沈工")
            continue

        approve_allocate_order(
            order_code, "APPROVE",
            APPROVER_LIST[index % len(APPROVER_LIST)], apply_day,
            "同意调配，请按计划出库并登记归还验收", "设备科-沈工")

        if target_state == "APPROVED":
            continue

        out_day = date_to_text(today + datetime.timedelta(days=apply_offset + 1))
        execute_allocate_order(order_code, out_day, "设备科-沈工")

        if target_state == "OUT":
            continue

        items = fetch_order_items(order_code)
        # 已归档单据逐台归还，其中第 2 张单的一台设备演示损坏联动维修工单
        return_limit = 1 if target_state == "PARTIAL" else len(items)
        for position, item in enumerate(items[:return_limit]):
            condition = "DAMAGE" if (index == 1 and position == 0) else (
                "WEAR" if (position == 1 and index == 0) else "GOOD")
            note = ("归还验收发现外壳破损且功能异常，需转维修工单" if
                    condition == "DAMAGE" else
                    "外观轻微磨损，功能正常，可继续投入使用" if
                    condition == "WEAR" else "设备完好、附件齐全，验收通过")
            return_allocate_item(
                item["item_code"], condition, note,
                date_to_text(today + datetime.timedelta(
                    days=apply_offset + loan_days if loan_days else
                    apply_offset + 5)), "设备科-沈工")

        if target_state == "DONE":
            close_allocate_order(
                order_code,
                date_to_text(today + datetime.timedelta(
                    days=apply_offset + loan_days if loan_days else
                    apply_offset + 6)),
                "全部设备归还验收完成，整单归档", "设备科-沈工")

    # 追加一张驳回单与一张作废单，覆盖状态完整取值（设备序号取台账有效范围）
    extra_codes = [device_list[6]["asset_code"]] if len(device_list) > 6 else []
    if extra_codes:
        rejected = save_allocate_order(
            "", "门诊临时借用输液泵（不批准）", "LOAN", "急诊医学科",
            ",".join(extra_codes), "门诊高峰临时增配输液泵",
            APPLICANT_LIST[0], date_to_text(today + datetime.timedelta(days=-3)),
            date_to_text(today + datetime.timedelta(days=4)),
            "演示数据：审批驳回", "设备科-沈工")
        if rejected.get("success"):
            amount += 1
            approve_allocate_order(
                rejected["order_code"], "REJECT", APPROVER_LIST[1],
                date_to_text(today + datetime.timedelta(days=-2)),
                "设备处于维修中，不具备借用条件", "设备科-沈工")
        void_codes = [device_list[7]["asset_code"]] if len(device_list) > 7 else []
        if void_codes:
            voided = save_allocate_order(
                "", "科研共享设备调配（已作废）", "RESEARCH", "检验医学中心",
                ",".join(void_codes), "原科研计划取消",
                APPLICANT_LIST[2], date_to_text(today + datetime.timedelta(days=-1)),
                date_to_text(today + datetime.timedelta(days=10)),
                "演示数据：申请作废", "设备科-沈工")
            if voided.get("success"):
                amount += 1
                cancel_allocate_order(
                    voided["order_code"], "科研项目取消，无需调配设备",
                    "设备科-沈工")

    # 超期催还扫描，标记借用类逾期未归还单据
    scan_overdue_allocate("设备科-沈工")
    return amount

# --------------------------------------------------------------------------
# 第 14 天数据表脚本增量：mdm_schema.sql（M，已内联为字符串常量 DAY14_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY14_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 14 天新增：设备调配与借用管理三表
-- ==================================================================
-- 调配申请单表：按「一次院内调配/借用一张申请单」登记调出调入科室、调配
-- 类型、申请事由、预计归还日期与审批执行进度，支撑设备跨科室流转全过程跟踪
CREATE TABLE IF NOT EXISTS md_allocate_order (
    order_id        INTEGER PRIMARY KEY AUTOINCREMENT, -- 申请单主键
    order_code      TEXT    NOT NULL UNIQUE,           -- 申请单编号（AL-序号）
    apply_title     TEXT    DEFAULT '',                -- 调配事由标题
    allocate_type   TEXT    DEFAULT 'LOAN',            -- 调配类型（见 ALLOCATE_TYPE_DICT）
    from_dept       TEXT    DEFAULT '',                -- 调出科室
    to_dept         TEXT    DEFAULT '',                -- 调入科室
    apply_reason    TEXT    DEFAULT '',                -- 申请事由说明
    applicant_name  TEXT    DEFAULT '',                -- 申请人
    apply_date      TEXT    DEFAULT '',                -- 申请日期
    due_date        TEXT    DEFAULT '',                -- 预计归还日期（借用类）
    approver_name   TEXT    DEFAULT '',                -- 审批人
    approve_date    TEXT    DEFAULT '',                -- 审批日期
    out_date        TEXT    DEFAULT '',                -- 出库执行日期
    back_date       TEXT    DEFAULT '',                -- 归还归档日期
    order_state     TEXT    DEFAULT 'WAIT',            -- 单据状态（见 ORDER_STATE_DICT）
    item_total      INTEGER DEFAULT 0,                 -- 调配设备台数
    item_back       INTEGER DEFAULT 0,                 -- 已归还台数
    item_loss       INTEGER DEFAULT 0,                 -- 归还异常台数
    back_condition  TEXT    DEFAULT '',                -- 归还完好状况汇总
    overdue_flag    INTEGER DEFAULT 0,                 -- 是否超期未归还
    remark_text     TEXT    DEFAULT '',                -- 备注说明
    operator_name   TEXT    DEFAULT '',                -- 登记人
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 调配设备明细表：一张申请单可登记多台设备，逐台记录出库与归还验收情况
CREATE TABLE IF NOT EXISTS md_allocate_item (
    item_id         INTEGER PRIMARY KEY AUTOINCREMENT, -- 明细主键
    item_code       TEXT    NOT NULL UNIQUE,           -- 明细编号（ALI-序号）
    order_code      TEXT    DEFAULT '',                -- 所属申请单编号
    asset_code      TEXT    DEFAULT '',                -- 设备资产编号
    asset_name      TEXT    DEFAULT '',                -- 设备名称快照
    asset_model     TEXT    DEFAULT '',                -- 规格型号快照
    original_price  REAL    DEFAULT 0,                 -- 设备原值快照（元）
    from_dept       TEXT    DEFAULT '',                -- 原使用科室快照
    to_dept         TEXT    DEFAULT '',                -- 目标使用科室快照
    item_state      TEXT    DEFAULT 'WAIT',            -- 明细状态（见 ITEM_STATE_DICT）
    out_date        TEXT    DEFAULT '',                -- 出库日期
    back_date       TEXT    DEFAULT '',                -- 归还日期
    back_condition  TEXT    DEFAULT '',                -- 归还完好状况（见 BACK_CONDITION_DICT）
    back_note       TEXT    DEFAULT '',                -- 归还验收说明
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 调配流转记录表：登记申请、审批、出库、归还、归档等节点的状态流转留痕
CREATE TABLE IF NOT EXISTS md_allocate_flow (
    flow_id         INTEGER PRIMARY KEY AUTOINCREMENT, -- 流转主键
    flow_code       TEXT    NOT NULL UNIQUE,           -- 流转编号（ALF-序号）
    order_code      TEXT    DEFAULT '',                -- 所属申请单编号
    action_name     TEXT    DEFAULT '',                -- 动作名称
    from_state      TEXT    DEFAULT '',                -- 变更前状态
    to_state        TEXT    DEFAULT '',                -- 变更后状态
    action_desc     TEXT    DEFAULT '',                -- 动作说明
    operator_name   TEXT    DEFAULT '',                -- 操作人
    action_time     TEXT    DEFAULT '',                -- 动作时间
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_alorder_code       ON md_allocate_order (order_code);
CREATE INDEX IF NOT EXISTS idx_alorder_type       ON md_allocate_order (allocate_type);
CREATE INDEX IF NOT EXISTS idx_alorder_state      ON md_allocate_order (order_state);
CREATE INDEX IF NOT EXISTS idx_alorder_due        ON md_allocate_order (due_date);
CREATE INDEX IF NOT EXISTS idx_alitem_code        ON md_allocate_item (item_code);
CREATE INDEX IF NOT EXISTS idx_alitem_order       ON md_allocate_item (order_code);
CREATE INDEX IF NOT EXISTS idx_alitem_state       ON md_allocate_item (item_state);
CREATE INDEX IF NOT EXISTS idx_alflow_code        ON md_allocate_flow (flow_code);
CREATE INDEX IF NOT EXISTS idx_alflow_order       ON md_allocate_flow (order_code);
"""


def apply_day14_schema(conn):
    """把第 14 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY14_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 14 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

from mdm_allocate import (
    build_allocate_payload,
    build_allocate_brief,
    save_allocate_order,
    add_allocate_item,
    remove_allocate_item,
    approve_allocate_order,
    execute_allocate_order,
    return_allocate_item,
    close_allocate_order,
    cancel_allocate_order,
    scan_overdue_allocate,
    seed_demo_allocate,
    ALLOCATE_TYPE_DICT,
    ORDER_STATE_DICT,
    ITEM_STATE_DICT,
    BACK_CONDITION_DICT,
)

# 【函数内插入】def api_overview():
#    # 第 14 天新增：总览叠加设备调配摘要，供调配概况卡渲染
#    payload["allocate_brief"] = build_allocate_brief()

@app.route("/md/allocate")
def panel_allocate():
    """设备调配与借用管理页面：申请登记、审批出库、归还验收与超期催还（第 14 天）。"""
    write_ops_journal("设备调配", "查询", "PANEL-ALLOCATE",
                      "打开设备调配与借用管理页面")
    return render_template(
        "panel_allocate.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/md/allocate/panel")
def api_allocate_panel():
    """调配面板数据接口：概览指标、五类图表、申请单、设备明细与流转记录。"""
    payload = build_allocate_payload(
        keyword=request.args.get("keyword", ""),
        allocate_type=request.args.get("allocate_type", ""),
        order_state=request.args.get("order_state", ""),
        overdue_only=request.args.get("overdue_only", "0") in ("1", "true", "True"),
        item_order=request.args.get("item_order", ""),
        item_state=request.args.get("item_state", ""),
        item_keyword=request.args.get("item_keyword", ""),
        flow_order=request.args.get("flow_order", ""),
        page=request.args.get("page", "1"),
    )
    return jsonify(payload)


@app.route("/api/md/allocate/order/save", methods=["POST"])
def api_allocate_order_save():
    """调配申请登记接口：新建申请单并批量展开设备明细。"""
    body = request.get_json(silent=True) or {}
    result = save_allocate_order(
        body.get("order_code") or "",
        body.get("apply_title") or "",
        body.get("allocate_type") or "LOAN",
        body.get("to_dept") or "",
        body.get("asset_codes") or "",
        body.get("apply_reason") or "",
        body.get("applicant_name") or "",
        body.get("apply_date") or "",
        body.get("due_date") or "",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/allocate/item/add", methods=["POST"])
def api_allocate_item_add():
    """追加设备接口：为待审批申请单追加一台调配设备。"""
    body = request.get_json(silent=True) or {}
    result = add_allocate_item(
        body.get("order_code") or "",
        body.get("asset_code") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/allocate/item/remove", methods=["POST"])
def api_allocate_item_remove():
    """移除设备接口：删除尚未出库的调配设备明细。"""
    body = request.get_json(silent=True) or {}
    result = remove_allocate_item(
        body.get("item_code") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/allocate/order/approve", methods=["POST"])
def api_allocate_order_approve():
    """调配审批接口：批准进入待出库，驳回终止流转。"""
    body = request.get_json(silent=True) or {}
    result = approve_allocate_order(
        body.get("order_code") or "",
        body.get("result_code") or "APPROVE",
        body.get("approver_name") or "",
        body.get("approve_date") or "",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/allocate/order/out", methods=["POST"])
def api_allocate_order_out():
    """调配出库接口：回写台账归属科室、位置与责任人并登记状态流转。"""
    body = request.get_json(silent=True) or {}
    result = execute_allocate_order(
        body.get("order_code") or "",
        body.get("out_date") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/allocate/item/back", methods=["POST"])
def api_allocate_item_back():
    """归还验收接口：登记完好状况，损坏设备联动生成维修工单。"""
    body = request.get_json(silent=True) or {}
    result = return_allocate_item(
        body.get("item_code") or "",
        body.get("back_condition") or "GOOD",
        body.get("back_note") or "",
        body.get("back_date") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/allocate/order/close", methods=["POST"])
def api_allocate_order_close():
    """整单归档接口：全部设备归还验收后方可归档。"""
    body = request.get_json(silent=True) or {}
    result = close_allocate_order(
        body.get("order_code") or "",
        body.get("back_date") or "",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/allocate/order/cancel", methods=["POST"])
def api_allocate_order_cancel():
    """申请作废接口：已出库或已归档的申请单不可作废。"""
    body = request.get_json(silent=True) or {}
    result = cancel_allocate_order(
        body.get("order_code") or "",
        body.get("reason_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/allocate/scan", methods=["POST"])
def api_allocate_scan():
    """超期催还扫描接口：标记借用类逾期未归还的调配申请单。"""
    body = request.get_json(silent=True) or {}
    return jsonify({
        "result": "OK",
        "amount": scan_overdue_allocate((body.get("operator") or "admin").strip()),
    })


@app.route("/api/md/allocate/options")
def api_allocate_options():
    """调配下拉选项接口：调配类型、单据状态、明细状态与归还状况字典。"""
    return jsonify({
        "result": "OK",
        "type_options": [{"code": k, "label": v}
                         for k, v in ALLOCATE_TYPE_DICT.items()],
        "state_options": [{"code": k, "label": v}
                          for k, v in ORDER_STATE_DICT.items()],
        "item_state_options": [{"code": k, "label": v}
                               for k, v in ITEM_STATE_DICT.items()],
        "condition_options": [{"code": k, "label": v}
                              for k, v in BACK_CONDITION_DICT.items()],
    })



# 【函数内插入】def bootstrap_application():
#    seed_demo_allocate()

# 【函数内插入】if __name__ == "__main__":
#    print("调配借用： http://127.0.0.1:%d/md/allocate" % PORT)

# --------------------------------------------------------------------------
# 第 14 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/mdm_allocate.js           新增 594 行
#   [修改] templates/layout_mdm.html        新增 1 行
#   [新增] templates/panel_allocate.html    新增 427 行
