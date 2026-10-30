# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-30（第 10 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 10 天新增与修改的源代码，不含第 1~9 天已有代码
#           本次迭代主题：备件与耗材库存管理模块 + 备件库存页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5001/md/overview）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 10 天新增业务模块 1：mdm_spare.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-30（第 10 天 / 共 15 天）
# 模块功能：备件与耗材库存管理模块。实现配件、耗材、工具与功能模块四类备件
#           的档案登记维护，采购入库、领用出库、归还入库与盘点调整四类出入库
#           流水登记，流水登记后自动回写当前库存与库存状态，并支持低库存扫描
#           自动生成补货计划、补货下单与到货入库确认；输出备件类型分布、库存
#           状态分布、库存金额排行、近 6 个月出入库趋势与领用科室排行五类统计
#           图表，形成「建档案 → 入库存 → 领用出库 → 低储预警 → 补货到货」
#           的备件全生命周期库存管理闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from mdm_dal import get_connection, write_ops_journal

# ------------------------------------------------------------------
# 备件类型字典：区分配件、耗材、工具与功能模块
# ------------------------------------------------------------------
SPARE_TYPE_DICT = {
    "PART": "设备配件",
    "CONSUME": "医用耗材",
    "TOOL": "维修工具",
    "MODULE": "功能模块",
}

# 库存状态字典：由当前库存与安全库存、积压阈值自动回算
STOCK_STATE_DICT = {
    "NORMAL": "库存充足",
    "WARN": "低于安全库存",
    "SHORT": "库存短缺",
    "OVER": "库存积压",
}

# 出入库流水类型字典
FLOW_TYPE_DICT = {
    "IN": "采购入库",
    "OUT": "领用出库",
    "RETURN": "归还入库",
    "CHECK": "盘点调整",
}

# 流水方向映射：入库为正、出库为负，盘点调整按实际登记数量处理
FLOW_DIRECTION_DICT = {
    "IN": 1,
    "OUT": -1,
    "RETURN": 1,
    "CHECK": 1,
}

# 补货计划状态字典
REPLENISH_STATE_DICT = {
    "WAIT": "待采购",
    "ORDER": "已下单",
    "DONE": "已到货",
    "VOID": "已取消",
}

# 库存状态判定阈值：低于安全库存六成判定短缺，高于积压阈值判定积压
SHORT_STOCK_RATE = 0.6
DEFAULT_OVER_STOCK = 200
DEFAULT_SAFE_STOCK = 10
REPLENISH_TIMES = 2

# 分页尺寸：备件档案与出入库流水各自独立分页
SPARE_PAGE_SIZE = 8
FLOW_PAGE_SIZE = 10

# 演示备件清单，用于首日灌入演示档案
DEMO_SPARE_LIST = (
    ("超声探头晶体模块", "MODULE", "C5-2P", "个", 8600, 4),
    ("监护仪血氧探头", "PART", "SPO2-A1", "个", 780, 12),
    ("输液泵蠕动片", "CONSUME", "PP-200", "片", 45, 60),
    ("CT 球管冷却油", "CONSUME", "CO-800", "升", 320, 20),
    ("呼吸机流量传感器", "PART", "FS-350", "个", 1450, 8),
    ("生化分析仪比色杯", "CONSUME", "CU-120", "盒", 260, 40),
    ("万用表校准工具包", "TOOL", "TK-100", "套", 680, 6),
    ("DR 平板探测器模块", "MODULE", "FPD-17", "块", 18500, 3),
    ("内窥镜冷光源灯泡", "CONSUME", "LB-300", "只", 520, 15),
    ("除颤仪电池组", "PART", "BAT-500", "组", 1980, 10),
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


def judge_stock_state(current_stock, safe_stock, over_stock=0):
    """按当前库存与安全库存、积压阈值判定库存状态编码。

    :param current_stock: 当前库存
    :param safe_stock: 安全库存
    :param over_stock: 积压预警阈值，为 0 时取默认阈值
    :return: 库存状态编码
    """
    safe = safe_stock if safe_stock > 0 else DEFAULT_SAFE_STOCK
    over = over_stock if over_stock > 0 else DEFAULT_OVER_STOCK
    if current_stock <= 0:
        return "SHORT"
    if current_stock < safe * SHORT_STOCK_RATE:
        return "SHORT"
    if current_stock < safe:
        return "WARN"
    if current_stock > over:
        return "OVER"
    return "NORMAL"


def build_spare_code(seq_no):
    """按序号拼装备件编码，如 SP-001。"""
    return "SP-%03d" % seq_no


def build_flow_code(seq_no):
    """按日期与序号拼装出入库流水编号，如 SF-20261030-001。"""
    return "SF-%s-%03d" % (today_date().strftime("%Y%m%d"), seq_no)


def build_plan_code(seq_no):
    """按日期与序号拼装补货计划编号，如 RP-20261030-001。"""
    return "RP-%s-%03d" % (today_date().strftime("%Y%m%d"), seq_no)


def next_seq_no(table_name):
    """读取指定业务表的记录数并加一，用于拼装业务编码。"""
    conn = get_connection()
    try:
        amount = conn.execute(
            "SELECT COUNT(1) AS c FROM %s" % table_name).fetchone()["c"]
    finally:
        conn.close()
    return amount + 1


def fetch_spare_row(spare_code):
    """按备件编码读取档案记录，未找到返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_spare_profile WHERE spare_code=?",
            (spare_code,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def fetch_vendor_name(vendor_code):
    """按供应商编码读取供应商名称，未找到返回空串。"""
    if not vendor_code:
        return ""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT vendor_name FROM md_vendor_profile WHERE vendor_code=?",
            (vendor_code,)).fetchone()
    finally:
        conn.close()
    return row["vendor_name"] if row is not None else ""


def fetch_device_options(limit=60):
    """读取台账设备候选，供领用出库弹窗选择关联设备。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT asset_code, asset_name, dept_name FROM md_asset_ledger "
            "ORDER BY asset_id ASC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(item) for item in rows]


# ------------------------------------------------------------------
# 备件档案登记
# ------------------------------------------------------------------
def save_spare(spare_code, spare_name, spare_type="PART", spec_model="",
               unit_name="件", vendor_code="", stock_price=0, safe_stock=0,
               over_stock=0, current_stock=0, location_text="", shelf_no="",
               remark_text="", operator="admin"):
    """登记或更新一条备件耗材档案。

    传入已存在的备件编码时执行更新，否则按新编码建档；留空编码时由系统
    按序号自动生成，保证同一种备件在出入库与补货时可被唯一引用。

    :return: 含 success 与 message 的执行结果字典
    """
    spare_name = (spare_name or "").strip()
    if not spare_name:
        return {"success": False, "message": "备件名称不能为空"}
    if spare_type not in SPARE_TYPE_DICT:
        return {"success": False, "message": "备件类型 %s 不合法" % spare_type}
    try:
        price = float(stock_price or 0)
        safe = int(safe_stock or 0)
        over = int(over_stock or 0)
        stock = int(current_stock or 0)
    except (TypeError, ValueError):
        return {"success": False, "message": "数量与金额必须为数字"}
    if price < 0 or safe < 0 or over < 0 or stock < 0:
        return {"success": False, "message": "数量与金额不能为负数"}

    safe = safe or DEFAULT_SAFE_STOCK
    over = over or DEFAULT_OVER_STOCK
    vendor_name = fetch_vendor_name((vendor_code or "").strip())
    spare_code = (spare_code or "").strip()
    conn = get_connection()
    try:
        if spare_code:
            exist = conn.execute(
                "SELECT spare_id FROM md_spare_profile WHERE spare_code=?",
                (spare_code,)).fetchone()
        else:
            exist = None
            spare_code = build_spare_code(next_seq_no("md_spare_profile"))
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state = judge_stock_state(stock, safe, over)
        if exist is None:
            conn.execute(
                "INSERT INTO md_spare_profile "
                "(spare_code, spare_name, spare_type, spec_model, unit_name, "
                "vendor_code, vendor_name, stock_price, safe_stock, over_stock, "
                "current_stock, total_in, total_out, stock_state, location_text, "
                "shelf_no, remark_text, operator_name, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (spare_code, spare_name, spare_type, (spec_model or "").strip(),
                 (unit_name or "件").strip(), (vendor_code or "").strip(),
                 vendor_name, price, safe, over, stock, stock, 0, state,
                 (location_text or "").strip(), (shelf_no or "").strip(),
                 (remark_text or "").strip(), (operator or "admin").strip(), stamp))
            action = "建档"
        else:
            conn.execute(
                "UPDATE md_spare_profile SET spare_name=?, spare_type=?, "
                "spec_model=?, unit_name=?, vendor_code=?, vendor_name=?, "
                "stock_price=?, safe_stock=?, over_stock=?, current_stock=?, "
                "stock_state=?, location_text=?, shelf_no=?, remark_text=?, "
                "updated_at=? WHERE spare_code=?",
                (spare_name, spare_type, (spec_model or "").strip(),
                 (unit_name or "件").strip(), (vendor_code or "").strip(),
                 vendor_name, price, safe, over, stock, state,
                 (location_text or "").strip(), (shelf_no or "").strip(),
                 (remark_text or "").strip(), stamp, spare_code))
            action = "更新"
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("备件库存", action, spare_code,
                      "%s备件档案：%s（%s）"
                      % (action, spare_name, SPARE_TYPE_DICT[spare_type]),
                      (operator or "admin").strip())
    return {"success": True,
            "message": "备件档案%s成功，编码 %s" % (action, spare_code),
            "spare_code": spare_code}


# ------------------------------------------------------------------
# 出入库流水登记
# ------------------------------------------------------------------
def save_spare_flow(spare_code, flow_type="IN", flow_amount=0, unit_price=0,
                    relate_device="", relate_dept="", vendor_code="",
                    flow_date="", remark_text="", operator="admin"):
    """登记一条出入库流水，并自动回写备件档案的当前库存与库存状态。

    出库类流水需校验库存是否充足；盘点调整按实际盈亏数量登记，允许把库存
    调整为账实数，变动数量由「调整后数量 - 调整前数量」计算得到。

    :return: 含 success 与 message 的执行结果字典
    """
    if flow_type not in FLOW_TYPE_DICT:
        return {"success": False, "message": "流水类型 %s 不合法" % flow_type}
    try:
        amount = int(flow_amount or 0)
        price = float(unit_price or 0)
    except (TypeError, ValueError):
        return {"success": False, "message": "数量与单价必须为数字"}
    spare_row = fetch_spare_row((spare_code or "").strip())
    if spare_row is None:
        return {"success": False, "message": "备件编码 %s 不存在" % spare_code}
    if amount == 0:
        return {"success": False, "message": "变动数量不能为 0"}

    before_stock = spare_row["current_stock"]
    if flow_type == "CHECK":
        # 盘点调整：传入的变动数量视为盘点后的账面数量，差额作为盈亏入库
        delta = amount - before_stock
        after_stock = amount
    else:
        delta = amount * FLOW_DIRECTION_DICT[flow_type]
        after_stock = before_stock + delta
    if after_stock < 0:
        return {"success": False,
                "message": "出库数量超过当前库存，当前库存仅 %d %s"
                           % (before_stock, spare_row["unit_name"])}
    if flow_type == "OUT" and not (relate_device or "").strip() \
            and not (relate_dept or "").strip():
        return {"success": False, "message": "领用出库需填写关联设备或领用科室"}

    flow_code = build_flow_code(next_seq_no("md_spare_flow"))
    day_text = (flow_date or "").strip() or date_to_text(today_date())
    price = price or spare_row["stock_price"]
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO md_spare_flow "
            "(flow_code, spare_code, spare_name, flow_type, flow_amount, "
            "before_stock, after_stock, unit_price, relate_device, relate_dept, "
            "vendor_code, flow_date, operator_name, remark_text) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (flow_code, spare_row["spare_code"], spare_row["spare_name"],
             flow_type, delta, before_stock, after_stock, price,
             (relate_device or "").strip(), (relate_dept or "").strip(),
             (vendor_code or "").strip(), day_text,
             (operator or "admin").strip(), (remark_text or "").strip()))
        in_amount = spare_row["total_in"] + (delta if delta > 0 else 0)
        out_amount = spare_row["total_out"] + (abs(delta) if delta < 0 else 0)
        state = judge_stock_state(after_stock, spare_row["safe_stock"],
                                  spare_row["over_stock"])
        conn.execute(
            "UPDATE md_spare_profile SET current_stock=?, total_in=?, "
            "total_out=?, stock_state=?, updated_at=? WHERE spare_code=?",
            (after_stock, in_amount, out_amount, state,
             datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             spare_row["spare_code"]))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("备件库存", "出入库", flow_code,
                      "%s %s %d %s，库存 %d → %d"
                      % (spare_row["spare_name"], FLOW_TYPE_DICT[flow_type],
                         abs(delta), spare_row["unit_name"],
                         before_stock, after_stock),
                      (operator or "admin").strip())
    return {"success": True,
            "message": "%s登记成功，库存 %d → %d"
                       % (FLOW_TYPE_DICT[flow_type], before_stock, after_stock),
            "flow_code": flow_code,
            "after_stock": after_stock}


# ------------------------------------------------------------------
# 低库存扫描与补货计划
# ------------------------------------------------------------------
def scan_low_stock(operator="admin"):
    """扫描低于安全库存的备件并自动生成补货计划。

    已存在待采购或已下单计划的备件不重复生成，建议采购量按安全库存的
    倍数补齐现有缺口。

    :return: 本次新增的补货计划条数
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT spare_code, spare_name, current_stock, safe_stock, "
            "vendor_code, vendor_name, stock_price FROM md_spare_profile "
            "WHERE stock_state IN ('WARN','SHORT') ORDER BY spare_id").fetchall()
        pending = conn.execute(
            "SELECT spare_code FROM md_spare_replenish "
            "WHERE plan_state IN ('WAIT','ORDER')").fetchall()
    finally:
        conn.close()
    pending_set = {item["spare_code"] for item in pending}
    amount = 0
    for item in rows:
        if item["spare_code"] in pending_set:
            continue
        safe = item["safe_stock"] if item["safe_stock"] > 0 else DEFAULT_SAFE_STOCK
        suggest = safe * REPLENISH_TIMES - item["current_stock"]
        if suggest <= 0:
            continue
        plan_code = build_plan_code(next_seq_no("md_spare_replenish"))
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO md_spare_replenish "
                "(plan_code, spare_code, spare_name, current_stock, safe_stock, "
                "suggest_amount, suggest_vendor, plan_amount, plan_state, "
                "operator_name, remark_text) "
                "VALUES (?,?,?,?,?,?,?,?,'WAIT',?,?)",
                (plan_code, item["spare_code"], item["spare_name"],
                 item["current_stock"], safe, suggest,
                 item["vendor_name"] or "", round(suggest * (item["stock_price"] or 0), 2),
                 (operator or "admin").strip(),
                 "低库存扫描自动生成，建议按安全库存 %d 的两倍补齐" % safe))
            conn.commit()
        finally:
            conn.close()
        amount += 1
    if amount:
        write_ops_journal("备件库存", "低储扫描", "SCAN-REPLENISH",
                          "低库存扫描新增补货计划 %d 条" % amount,
                          (operator or "admin").strip())
    return amount


def order_replenish(plan_code, order_date="", remark_text="", operator="admin"):
    """补货计划下单：把待采购计划置为已下单并记录下单日期。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_spare_replenish WHERE plan_code=?",
            ((plan_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "补货计划 %s 不存在" % plan_code}
        if row["plan_state"] != "WAIT":
            return {"success": False,
                    "message": "仅待采购计划可下单，当前状态为「%s」"
                               % REPLENISH_STATE_DICT.get(row["plan_state"], "未知")}
        conn.execute(
            "UPDATE md_spare_replenish SET plan_state='ORDER', order_date=?, "
            "remark_text=?, operator_name=? WHERE plan_code=?",
            ((order_date or "").strip() or date_to_text(today_date()),
             (remark_text or "").strip(), (operator or "admin").strip(),
             row["plan_code"]))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("备件库存", "补货下单", plan_code,
                      "补货计划 %s 已下单，采购 %s %d 件"
                      % (plan_code, row["spare_name"], row["suggest_amount"]),
                      (operator or "admin").strip())
    return {"success": True, "message": "补货计划已下单，等待到货入库"}


def arrive_replenish(plan_code, arrive_amount=0, arrive_date="",
                     operator="admin"):
    """补货到货确认：登记采购入库流水并把计划置为已到货。

    :return: 含 success 与 message 的执行结果字典
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_spare_replenish WHERE plan_code=?",
            ((plan_code or "").strip(),)).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"success": False, "message": "补货计划 %s 不存在" % plan_code}
    if row["plan_state"] not in ("WAIT", "ORDER"):
        return {"success": False, "message": "该计划已到货或已取消，无需重复确认"}
    try:
        amount = int(arrive_amount or 0) or row["suggest_amount"]
    except (TypeError, ValueError):
        amount = row["suggest_amount"]
    if amount <= 0:
        return {"success": False, "message": "到货数量必须大于 0"}

    outcome = save_spare_flow(
        row["spare_code"], "IN", amount, 0, "", "",
        "", (arrive_date or "").strip(),
        "补货计划 %s 到货入库" % row["plan_code"], (operator or "admin").strip())
    if not outcome.get("success"):
        return outcome
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE md_spare_replenish SET plan_state='DONE', arrive_date=?, "
            "operator_name=? WHERE plan_code=?",
            ((arrive_date or "").strip() or date_to_text(today_date()),
             (operator or "admin").strip(), row["plan_code"]))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("备件库存", "补货到货", plan_code,
                      "补货计划 %s 到货入库 %d 件" % (plan_code, amount),
                      (operator or "admin").strip())
    return {"success": True,
            "message": "到货入库成功，%s 入库 %d 件"
                       % (row["spare_name"], amount)}


def cancel_replenish(plan_code, operator="admin"):
    """取消一条尚未到货的补货计划。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_spare_replenish WHERE plan_code=?",
            ((plan_code or "").strip(),)).fetchone()
        if row is None:
            return {"success": False, "message": "补货计划 %s 不存在" % plan_code}
        if row["plan_state"] == "DONE":
            return {"success": False, "message": "已到货计划不能取消"}
        conn.execute(
            "UPDATE md_spare_replenish SET plan_state='VOID', operator_name=? "
            "WHERE plan_code=?",
            ((operator or "admin").strip(), row["plan_code"]))
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": "补货计划已取消"}


# ------------------------------------------------------------------
# 分页查询
# ------------------------------------------------------------------
def fetch_spares(keyword="", spare_type="", stock_state="", vendor_code="",
                 page=1):
    """按组合条件分页查询备件档案，并附带库存状态与库存金额。"""
    where = []
    params = []
    if keyword:
        where.append("(spare_code LIKE ? OR spare_name LIKE ? OR "
                     "spec_model LIKE ? OR location_text LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like, like, like])
    if spare_type:
        where.append("spare_type=?")
        params.append(spare_type)
    if stock_state:
        where.append("stock_state=?")
        params.append(stock_state)
    if vendor_code:
        where.append("vendor_code=?")
        params.append(vendor_code)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    if page_no < 1:
        page_no = 1
    offset = (page_no - 1) * SPARE_PAGE_SIZE

    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_spare_profile" + clause,
            params).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM md_spare_profile" + clause +
            " ORDER BY stock_state DESC, spare_id ASC LIMIT ? OFFSET ?",
            params + [SPARE_PAGE_SIZE, offset]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        row = dict(item)
        row["type_label"] = SPARE_TYPE_DICT.get(row["spare_type"], row["spare_type"])
        row["state_label"] = STOCK_STATE_DICT.get(row["stock_state"], row["stock_state"])
        row["stock_amount"] = round(row["current_stock"] * row["stock_price"], 2)
        row["stock_rate"] = round(row["current_stock"] * 100.0 / row["safe_stock"], 1) \
            if row["safe_stock"] else 0.0
        records.append(row)
    return {
        "records": records,
        "total": total,
        "page": page_no,
        "page_size": SPARE_PAGE_SIZE,
        "page_amount": max(1, (total + SPARE_PAGE_SIZE - 1) // SPARE_PAGE_SIZE),
    }


def fetch_spare_flows(spare_code="", flow_type="", keyword="", page=1):
    """按组合条件分页查询出入库流水，并附带变动方向与金额。"""
    where = []
    params = []
    if spare_code:
        where.append("spare_code=?")
        params.append(spare_code)
    if flow_type:
        where.append("flow_type=?")
        params.append(flow_type)
    if keyword:
        where.append("(flow_code LIKE ? OR spare_name LIKE ? OR "
                     "relate_device LIKE ? OR relate_dept LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like, like, like])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    if page_no < 1:
        page_no = 1
    offset = (page_no - 1) * FLOW_PAGE_SIZE

    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_spare_flow" + clause,
            params).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM md_spare_flow" + clause +
            " ORDER BY flow_id DESC LIMIT ? OFFSET ?",
            params + [FLOW_PAGE_SIZE, offset]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        row = dict(item)
        row["type_label"] = FLOW_TYPE_DICT.get(row["flow_type"], row["flow_type"])
        row["direction"] = "入" if row["flow_amount"] > 0 else "出"
        row["flow_total"] = round(abs(row["flow_amount"]) * row["unit_price"], 2)
        records.append(row)
    return {
        "records": records,
        "total": total,
        "page": page_no,
        "page_size": FLOW_PAGE_SIZE,
        "page_amount": max(1, (total + FLOW_PAGE_SIZE - 1) // FLOW_PAGE_SIZE),
    }


def fetch_replenish_plans(plan_state="", limit=30):
    """查询补货计划清单，默认返回最近 30 条。"""
    sql = "SELECT * FROM md_spare_replenish"
    params = []
    if plan_state:
        sql += " WHERE plan_state=?"
        params.append(plan_state)
    sql += " ORDER BY plan_id DESC LIMIT ?"
    params.append(limit)
    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    result = []
    for item in rows:
        row = dict(item)
        row["state_label"] = REPLENISH_STATE_DICT.get(row["plan_state"],
                                                      row["plan_state"])
        result.append(row)
    return result


# ------------------------------------------------------------------
# 统计与图表
# ------------------------------------------------------------------
def build_spare_summary():
    """汇总备件库存核心指标：档案量、库存金额、短缺量、预警量与补货在途量。"""
    conn = get_connection()
    try:
        spare_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_spare_profile").fetchone()["c"]
        short_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM md_spare_profile WHERE stock_state='SHORT'"
        ).fetchone()["c"]
        warn_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM md_spare_profile WHERE stock_state='WARN'"
        ).fetchone()["c"]
        over_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM md_spare_profile WHERE stock_state='OVER'"
        ).fetchone()["c"]
        stock_row = conn.execute(
            "SELECT COALESCE(SUM(current_stock*stock_price),0) AS s, "
            "COALESCE(SUM(current_stock),0) AS q FROM md_spare_profile").fetchone()
        flow_row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN flow_type='IN' THEN flow_amount "
            "ELSE 0 END),0) AS in_q, "
            "COALESCE(SUM(CASE WHEN flow_type='OUT' THEN -flow_amount "
            "ELSE 0 END),0) AS out_q FROM md_spare_flow").fetchone()
        plan_row = conn.execute(
            "SELECT COUNT(1) AS c, COALESCE(SUM(plan_amount),0) AS s "
            "FROM md_spare_replenish WHERE plan_state IN ('WAIT','ORDER')"
        ).fetchone()
    finally:
        conn.close()
    return {
        "spare_total": spare_total,
        "short_amount": short_amount,
        "warn_amount": warn_amount,
        "over_amount": over_amount,
        "stock_quantity": int(stock_row["q"]),
        "stock_amount": round(stock_row["s"], 2),
        "in_quantity": int(flow_row["in_q"]),
        "out_quantity": int(flow_row["out_q"]),
        "plan_amount": plan_row["c"],
        "plan_money": round(plan_row["s"], 2),
    }


def build_spare_charts():
    """构建五类图表数据：类型分布、库存状态分布、库存金额排行、近 6 月出入
    库趋势与领用科室排行。"""
    conn = get_connection()
    try:
        spare_rows = conn.execute(
            "SELECT spare_code, spare_name, spare_type, stock_state, "
            "current_stock, stock_price FROM md_spare_profile").fetchall()
        flow_rows = conn.execute(
            "SELECT flow_type, flow_amount, relate_dept, flow_date, unit_price "
            "FROM md_spare_flow").fetchall()
    finally:
        conn.close()

    type_counter = {}
    for item in spare_rows:
        type_counter[item["spare_type"]] = type_counter.get(item["spare_type"], 0) + 1
    type_distribution = [
        {"name": SPARE_TYPE_DICT.get(code, code), "value": amount}
        for code, amount in type_counter.items()
    ]

    state_counter = {}
    for item in spare_rows:
        state_counter[item["stock_state"]] = state_counter.get(item["stock_state"], 0) + 1
    state_distribution = [
        {"name": STOCK_STATE_DICT.get(code, code), "value": state_counter.get(code, 0)}
        for code in ("NORMAL", "WARN", "SHORT", "OVER")
    ]

    stock_rank = sorted(
        [{"name": item["spare_name"],
          "value": round(item["current_stock"] * item["stock_price"], 2)}
         for item in spare_rows],
        key=lambda x: x["value"], reverse=True)[:6]

    month_keys = []
    cursor = today_date().replace(day=1)
    for _ in range(6):
        month_keys.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - datetime.timedelta(days=1)).replace(day=1)
    month_keys.reverse()
    in_counter = {key: 0 for key in month_keys}
    out_counter = {key: 0 for key in month_keys}
    for item in flow_rows:
        day = parse_date_text(item["flow_date"])
        if day is None:
            continue
        key = day.strftime("%Y-%m")
        if key not in in_counter:
            continue
        if item["flow_amount"] > 0:
            in_counter[key] += item["flow_amount"]
        else:
            out_counter[key] += abs(item["flow_amount"])

    dept_counter = {}
    for item in flow_rows:
        if item["flow_type"] != "OUT":
            continue
        dept = (item["relate_dept"] or "").strip() or "未指定科室"
        dept_counter[dept] = dept_counter.get(dept, 0) + abs(item["flow_amount"])
    dept_rank = sorted(
        [{"name": key, "value": value} for key, value in dept_counter.items()],
        key=lambda x: x["value"], reverse=True)[:6]

    return {
        "type_distribution": type_distribution,
        "state_distribution": state_distribution,
        "stock_rank": stock_rank,
        "month_trend": {
            "months": month_keys,
            "in_values": [in_counter[key] for key in month_keys],
            "out_values": [out_counter[key] for key in month_keys],
        },
        "dept_rank": dept_rank,
    }


def fetch_spare_options():
    """备件库存页下拉选项：各类字典项、备件候选、供应商候选与设备候选。"""
    conn = get_connection()
    try:
        spare_rows = conn.execute(
            "SELECT spare_code, spare_name, current_stock, unit_name "
            "FROM md_spare_profile ORDER BY spare_id").fetchall()
        vendor_rows = conn.execute(
            "SELECT vendor_code, vendor_name FROM md_vendor_profile "
            "WHERE vendor_state='RUN' ORDER BY vendor_id").fetchall()
    finally:
        conn.close()
    return {
        "spare_list": [dict(item) for item in spare_rows],
        "vendor_list": [dict(item) for item in vendor_rows],
        "device_list": fetch_device_options(),
        "type_options": [{"code": k, "label": v} for k, v in SPARE_TYPE_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in STOCK_STATE_DICT.items()],
        "flow_options": [{"code": k, "label": v} for k, v in FLOW_TYPE_DICT.items()],
        "plan_options": [{"code": k, "label": v}
                         for k, v in REPLENISH_STATE_DICT.items()],
    }


def build_spare_payload(keyword="", spare_type="", stock_state="",
                        vendor_code="", flow_spare="", flow_type="",
                        flow_keyword="", plan_state="",
                        spare_page=1, flow_page=1):
    """组装备件与耗材库存管理页面所需的全部数据。"""
    scan_low_stock()
    spare_pager = fetch_spares(keyword, spare_type, stock_state,
                               vendor_code, spare_page)
    flow_pager = fetch_spare_flows(flow_spare, flow_type, flow_keyword, flow_page)
    return {
        "summary": build_spare_summary(),
        "charts": build_spare_charts(),
        "spares": spare_pager["records"],
        "spare_total": spare_pager["total"],
        "spare_page": spare_pager["page"],
        "spare_page_amount": spare_pager["page_amount"],
        "flows": flow_pager["records"],
        "flow_total": flow_pager["total"],
        "flow_page": flow_pager["page"],
        "flow_page_amount": flow_pager["page_amount"],
        "plans": fetch_replenish_plans(plan_state),
        "options": fetch_spare_options(),
        "result": "OK",
    }


def build_spare_brief():
    """备件库存看板摘要，供运维总览看板嵌入。"""
    summary = build_spare_summary()
    return {
        "spare_total": summary["spare_total"],
        "stock_amount": summary["stock_amount"],
        "short_amount": summary["short_amount"],
        "warn_amount": summary["warn_amount"],
        "plan_amount": summary["plan_amount"],
        "out_quantity": summary["out_quantity"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_spare():
    """灌入备件库存演示数据：建档、采购入库、领用出库与低储扫描补货。

    :return: 生成的出入库流水条数
    """
    conn = get_connection()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM md_spare_profile").fetchone()["c"]
        if already:
            return 0
    finally:
        conn.close()

    today = today_date()
    vendor_rows = []
    conn = get_connection()
    try:
        vendor_rows = conn.execute(
            "SELECT vendor_code, vendor_name FROM md_vendor_profile "
            "ORDER BY vendor_id").fetchall()
    finally:
        conn.close()
    vendor_codes = [item["vendor_code"] for item in vendor_rows]

    device_list = fetch_device_options(limit=12)
    dept_pool = ("急诊科", "重症医学科", "影像科", "检验科", "手术室", "心内科")

    spare_codes = []
    spare_targets = {}
    for index, (name, spare_type, spec, unit, price, safe) \
            in enumerate(DEMO_SPARE_LIST):
        vendor_code = vendor_codes[index % len(vendor_codes)] if vendor_codes else ""
        # 演示分布：三类备件压到安全库存以下或附近，用于展示短缺与预警状态；
        # 另有库存积压样本，四类库存状态在演示数据中均有体现
        if index == 1:
            stock = int(safe * 0.25)
        elif index == 4:
            stock = int(safe * 0.3)
        elif index == 8:
            stock = int(safe * 0.7)
        elif index == 5:
            stock = int(safe * 6)
        else:
            stock = int(safe * (1.2 + (index % 3) * 0.5))
        outcome = save_spare(
            "", name, spare_type, spec, unit, vendor_code, price,
            safe, DEFAULT_OVER_STOCK, 0,
            "医工科备件库 %d 号库房" % (index % 3 + 1),
            "A-%02d-%02d" % (index % 3 + 1, index + 1),
            "演示备件档案，含采购入库与领用出库记录", "医工科-周工")
        if outcome.get("success"):
            spare_codes.append(outcome["spare_code"])
            spare_targets[outcome["spare_code"]] = stock

    amount = 0
    for index, spare_code in enumerate(spare_codes):
        spare_row = fetch_spare_row(spare_code)
        if spare_row is None:
            continue
        # 采购入库：档案建账库存为零，按目标库存拆成两次入库流水累加到账
        stock = spare_targets.get(spare_code, 0)
        first = max(1, int(stock * 0.6))
        second = stock - first
        for step, quantity in enumerate((first, second)):
            if quantity <= 0:
                continue
            outcome = save_spare_flow(
                spare_code, "IN", quantity, spare_row["stock_price"], "", "",
                spare_row["vendor_code"],
                date_to_text(today - datetime.timedelta(days=120 - step * 45 + index)),
                "演示采购入库流水", "库房-孙老师")
            amount += 1 if outcome.get("success") else 0

    # 领用出库：选择库存充足的备件，按科室领用，制造出库流水与科室排行
    for index, spare_code in enumerate(spare_codes[:7]):
        spare_row = fetch_spare_row(spare_code)
        if spare_row is None or spare_row["current_stock"] < 3:
            continue
        device = device_list[index % len(device_list)] if device_list else None
        outcome = save_spare_flow(
            spare_code, "OUT", min(3 + index % 4, spare_row["current_stock"] - 1),
            spare_row["stock_price"],
            device["asset_code"] if device else "",
            device["dept_name"] if device else dept_pool[index % len(dept_pool)],
            "", date_to_text(today - datetime.timedelta(days=40 - index * 4)),
            "%s 维保领用备件" % (device["asset_name"] if device else "科室领用"),
            "维修组-李工")
        amount += 1 if outcome.get("success") else 0

    # 归还入库与盘点调整各补一条，丰富流水类型分布
    if len(spare_codes) >= 3:
        spare_row = fetch_spare_row(spare_codes[2])
        if spare_row is not None:
            outcome = save_spare_flow(
                spare_codes[2], "RETURN", 2, spare_row["stock_price"], "",
                "手术室", "", date_to_text(today - datetime.timedelta(days=15)),
                "演示归还入库流水", "维修组-李工")
            amount += 1 if outcome.get("success") else 0
        spare_row = fetch_spare_row(spare_codes[6]) if len(spare_codes) > 6 else None
        if spare_row is not None:
            outcome = save_spare_flow(
                spare_codes[6], "CHECK", max(1, spare_row["current_stock"] - 1),
                spare_row["stock_price"], "", "", "",
                date_to_text(today - datetime.timedelta(days=8)),
                "演示盘点调整流水，按账实数调整", "库房-孙老师")
            amount += 1 if outcome.get("success") else 0

    scan_low_stock("库房-孙老师")
    # 演示：把第一条补货计划推进到已下单，第二条直接到货入库形成闭环
    plans = fetch_replenish_plans(plan_state="WAIT")
    for index, plan in enumerate(plans[:2]):
        if index == 0:
            order_replenish(plan["plan_code"],
                            date_to_text(today - datetime.timedelta(days=2)),
                            "已向建议供应商下达采购订单", "库房-孙老师")
        else:
            arrive_replenish(plan["plan_code"], plan["suggest_amount"],
                             date_to_text(today - datetime.timedelta(days=1)),
                             "库房-孙老师")
            amount += 1
    return amount

# --------------------------------------------------------------------------
# 第 10 天数据表脚本增量：mdm_schema.sql（M，已内联为字符串常量 DAY10_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY10_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 10 天新增：备件耗材档案表
-- ==================================================================
-- 备件档案表：按「一种备件一条」管理配件、耗材、工具与功能模块四类物资的
-- 规格型号、计量单位、默认供应商、采购单价、安全库存与当前库存，库存状态由
-- 当前库存与安全库存、积压阈值自动回算，支撑备件库的账实一致管理
CREATE TABLE IF NOT EXISTS md_spare_profile (
    spare_id      INTEGER PRIMARY KEY AUTOINCREMENT,   -- 备件主键
    spare_code    TEXT    NOT NULL UNIQUE,             -- 备件编码（SP-三位序号）
    spare_name    TEXT    NOT NULL,                    -- 备件名称
    spare_type    TEXT    DEFAULT 'PART',              -- 备件类型（见 SPARE_TYPE_DICT）
    spec_model    TEXT    DEFAULT '',                  -- 规格型号
    unit_name     TEXT    DEFAULT '件',                -- 计量单位
    vendor_code   TEXT    DEFAULT '',                  -- 默认供应商编码
    vendor_name   TEXT    DEFAULT '',                  -- 默认供应商名称
    stock_price   REAL    DEFAULT 0,                   -- 采购单价（元）
    safe_stock    INTEGER DEFAULT 10,                  -- 安全库存数量
    over_stock    INTEGER DEFAULT 200,                 -- 积压预警数量
    current_stock INTEGER DEFAULT 0,                   -- 当前库存数量
    total_in      INTEGER DEFAULT 0,                   -- 累计入库数量
    total_out     INTEGER DEFAULT 0,                   -- 累计出库数量
    stock_state   TEXT    DEFAULT 'NORMAL',            -- 库存状态（见 STOCK_STATE_DICT）
    location_text TEXT    DEFAULT '',                  -- 存放库房
    shelf_no      TEXT    DEFAULT '',                  -- 货架货位号
    remark_text   TEXT    DEFAULT '',                  -- 备注说明
    operator_name TEXT    DEFAULT 'admin',             -- 档案登记人
    created_at    TEXT    DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT    DEFAULT ''
);

-- ==================================================================
-- 第 10 天新增：备件出入库流水表
-- ==================================================================
-- 出入库流水表：按「一次出入库一条」记录采购入库、领用出库、归还入库与盘点
-- 调整四类业务，登记变动前后的库存数量与关联设备科室，流水登记后自动回写
-- 备件档案的当前库存、累计出入库数量与库存状态，保证账实同步
CREATE TABLE IF NOT EXISTS md_spare_flow (
    flow_id       INTEGER PRIMARY KEY AUTOINCREMENT,   -- 流水主键
    flow_code     TEXT    NOT NULL UNIQUE,             -- 流水编号（SF-年月日-序号）
    spare_code    TEXT    NOT NULL,                    -- 备件编码
    spare_name    TEXT    DEFAULT '',                  -- 备件名称快照
    flow_type     TEXT    DEFAULT 'IN',                -- 流水类型（见 FLOW_TYPE_DICT）
    flow_amount   INTEGER DEFAULT 0,                   -- 变动数量（出库为负、入库为正）
    before_stock  INTEGER DEFAULT 0,                   -- 变动前库存
    after_stock   INTEGER DEFAULT 0,                   -- 变动后库存
    unit_price    REAL    DEFAULT 0,                   -- 变动单价（元）
    relate_device TEXT    DEFAULT '',                  -- 关联设备编号
    relate_dept   TEXT    DEFAULT '',                  -- 领用科室
    vendor_code   TEXT    DEFAULT '',                  -- 供应来源编码
    flow_date     TEXT    DEFAULT '',                  -- 业务发生日期
    operator_name TEXT    DEFAULT 'admin',             -- 登记人
    remark_text   TEXT    DEFAULT '',                  -- 业务备注
    created_at    TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 10 天新增：备件补货计划表
-- ==================================================================
-- 补货计划表：按「一次补货一条」记录由低库存扫描自动生成的补货建议，含建议
-- 采购量、建议供应商与预估金额，并跟踪下单、到货全过程；到货确认后自动登记
-- 采购入库流水并回写库存，形成「库存预警 → 补货下单 → 到货入库」闭环
CREATE TABLE IF NOT EXISTS md_spare_replenish (
    plan_id        INTEGER PRIMARY KEY AUTOINCREMENT,  -- 计划主键
    plan_code      TEXT    NOT NULL UNIQUE,            -- 计划编号（RP-年月日-序号）
    spare_code     TEXT    NOT NULL,                   -- 备件编码
    spare_name     TEXT    DEFAULT '',                 -- 备件名称快照
    current_stock  INTEGER DEFAULT 0,                  -- 扫描时库存
    safe_stock     INTEGER DEFAULT 0,                  -- 安全库存
    suggest_amount INTEGER DEFAULT 0,                  -- 建议采购量
    suggest_vendor TEXT    DEFAULT '',                 -- 建议供应商
    plan_amount    REAL    DEFAULT 0,                  -- 预估采购金额（元）
    plan_state     TEXT    DEFAULT 'WAIT',             -- 计划状态（见 REPLENISH_STATE_DICT）
    order_date     TEXT    DEFAULT '',                 -- 下单日期
    arrive_date    TEXT    DEFAULT '',                 -- 到货日期
    operator_name  TEXT    DEFAULT 'admin',            -- 计划经办人
    remark_text    TEXT    DEFAULT '',                 -- 计划备注
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_spare_code      ON md_spare_profile (spare_code);
CREATE INDEX IF NOT EXISTS idx_spare_type      ON md_spare_profile (spare_type);
CREATE INDEX IF NOT EXISTS idx_spare_state     ON md_spare_profile (stock_state);
CREATE INDEX IF NOT EXISTS idx_spare_vendor    ON md_spare_profile (vendor_code);
CREATE INDEX IF NOT EXISTS idx_flow_code       ON md_spare_flow (flow_code);
CREATE INDEX IF NOT EXISTS idx_flow_spare      ON md_spare_flow (spare_code);
CREATE INDEX IF NOT EXISTS idx_flow_type       ON md_spare_flow (flow_type);
CREATE INDEX IF NOT EXISTS idx_flow_date       ON md_spare_flow (flow_date);
CREATE INDEX IF NOT EXISTS idx_replen_code     ON md_spare_replenish (plan_code);
CREATE INDEX IF NOT EXISTS idx_replen_spare    ON md_spare_replenish (spare_code);
CREATE INDEX IF NOT EXISTS idx_replen_state    ON md_spare_replenish (plan_state);
"""


def apply_day10_schema(conn):
    """把第 10 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY10_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 10 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-30（第 10 天 / 共 15 天）
#           分析、盘点维保管理、运维日志查询、设备报废审批、计量校准管理、
#           供应商合同管理与备件耗材库存管理十套页面路由，并提供台账增删改
#           查、闲置识别扫描、状态切换、ROI 测算与年度收支档案维护、盘点任务
#           生成与账实核对、维保登记、运维日志检索登记闭环与 CSV 导出、报废
#           申请发起逐级审批流转与处置归档、校准计划编制、校准结果登记、证书
#           到期扫描与校准档案查询、供应商档案登记、合同签订与付款节点拆分
#           执行、合同到期扫描与供应商履约评级重算，以及备件耗材档案登记、
#           采购入库与领用出库流水登记、库存状态回算、低储扫描生成补货计划、
#           补货下单与到货入库确认等 RESTful 接口，供前端表格、弹窗与

from mdm_spare import (
    build_spare_payload,
    build_spare_brief,
    save_spare,
    fetch_spare_row,
    save_spare_flow,
    scan_low_stock,
    fetch_replenish_plans,
    order_replenish,
    arrive_replenish,
    cancel_replenish,
    seed_demo_spare,
    SPARE_TYPE_DICT,
    STOCK_STATE_DICT,
    FLOW_TYPE_DICT,
    REPLENISH_STATE_DICT,
)

# 【函数内插入】def api_overview():
#    # 第 10 天新增：总览叠加备件库存摘要，供备件概况卡渲染
#    payload["spare_brief"] = build_spare_brief()

# ==================================================================
# 以下为第 10 天新增：备件与耗材库存管理页面与接口
# ==================================================================


@app.route("/md/spare")
def panel_spare():
    """备件与耗材库存管理页面：库存概览、五类图表、档案流水与补货计划。"""
    write_ops_journal("备件库存", "查询", "PANEL-SPARE", "打开备件与耗材库存管理页面")
    return render_template(
        "panel_spare.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/md/spare/panel")
def api_spare_panel():
    """备件库存数据接口：汇总指标、五类图表、双分页明细、补货计划与选项。"""
    return jsonify(build_spare_payload(
        keyword=(request.args.get("keyword") or "").strip(),
        spare_type=(request.args.get("spare_type") or "").strip(),
        stock_state=(request.args.get("stock_state") or "").strip(),
        vendor_code=(request.args.get("vendor_code") or "").strip(),
        flow_spare=(request.args.get("flow_spare") or "").strip(),
        flow_type=(request.args.get("flow_type") or "").strip(),
        flow_keyword=(request.args.get("flow_keyword") or "").strip(),
        plan_state=(request.args.get("plan_state") or "").strip(),
        spare_page=int(request.args.get("spare_page", 1)),
        flow_page=int(request.args.get("flow_page", 1)),
    ))


@app.route("/api/md/spare/save", methods=["POST"])
def api_spare_save():
    """备件档案登记接口：传入编码则更新，留空编码则按序号建档。"""
    body = request.get_json(silent=True) or {}
    result = save_spare(
        (body.get("spare_code") or "").strip(),
        (body.get("spare_name") or "").strip(),
        (body.get("spare_type") or "PART").strip(),
        body.get("spec_model") or "",
        (body.get("unit_name") or "件").strip(),
        (body.get("vendor_code") or "").strip(),
        body.get("stock_price") or 0,
        body.get("safe_stock") or 0,
        body.get("over_stock") or 0,
        body.get("current_stock") or 0,
        body.get("location_text") or "",
        body.get("shelf_no") or "",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/spare/detail")
def api_spare_detail():
    """备件档案详情接口，供编辑弹窗回填表单。"""
    spare_row = fetch_spare_row((request.args.get("spare_code") or "").strip())
    return jsonify({
        "result": "OK" if spare_row else "FAIL",
        "spare": spare_row,
    })


@app.route("/api/md/spare/flow", methods=["POST"])
def api_spare_flow():
    """出入库流水登记接口：登记变动数量并自动回写当前库存与库存状态。"""
    body = request.get_json(silent=True) or {}
    result = save_spare_flow(
        (body.get("spare_code") or "").strip(),
        (body.get("flow_type") or "IN").strip(),
        body.get("flow_amount") or 0,
        body.get("unit_price") or 0,
        (body.get("relate_device") or "").strip(),
        (body.get("relate_dept") or "").strip(),
        (body.get("vendor_code") or "").strip(),
        (body.get("flow_date") or "").strip(),
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/spare/scan", methods=["POST"])
def api_spare_scan():
    """低库存扫描接口：为低于安全库存的备件自动生成补货计划。"""
    body = request.get_json(silent=True) or {}
    amount = scan_low_stock((body.get("operator") or "admin").strip())
    return jsonify({
        "result": "OK",
        "success": True,
        "amount": amount,
        "message": "低储扫描完成，新增补货计划 %d 条" % amount,
    })


@app.route("/api/md/spare/plans")
def api_spare_plans():
    """补货计划查询接口：按状态返回补货计划清单。"""
    return jsonify({
        "result": "OK",
        "plans": fetch_replenish_plans(
            (request.args.get("plan_state") or "").strip(),
            int(request.args.get("limit", 30))),
    })


@app.route("/api/md/spare/replenish/order", methods=["POST"])
def api_spare_replenish_order():
    """补货下单接口：把待采购计划置为已下单并记录下单日期。"""
    body = request.get_json(silent=True) or {}
    result = order_replenish(
        (body.get("plan_code") or "").strip(),
        (body.get("order_date") or "").strip(),
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/spare/replenish/arrive", methods=["POST"])
def api_spare_replenish_arrive():
    """补货到货接口：登记采购入库流水并把计划置为已到货。"""
    body = request.get_json(silent=True) or {}
    result = arrive_replenish(
        (body.get("plan_code") or "").strip(),
        body.get("arrive_amount") or 0,
        (body.get("arrive_date") or "").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/spare/replenish/cancel", methods=["POST"])
def api_spare_replenish_cancel():
    """补货计划取消接口：作废一条尚未到货的补货计划。"""
    body = request.get_json(silent=True) or {}
    result = cancel_replenish(
        (body.get("plan_code") or "").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/spare/options")
def api_spare_options():
    """备件库存下拉选项接口：备件类型、库存状态、流水类型与计划状态字典。"""
    return jsonify({
        "result": "OK",
        "type_options": [{"code": k, "label": v} for k, v in SPARE_TYPE_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in STOCK_STATE_DICT.items()],
        "flow_options": [{"code": k, "label": v} for k, v in FLOW_TYPE_DICT.items()],
        "plan_options": [{"code": k, "label": v}
                         for k, v in REPLENISH_STATE_DICT.items()],
    })



# 【函数内插入】def bootstrap_application():
#    seed_demo_spare()

# 【函数内插入】if __name__ == "__main__":
#    print("备件库存： http://127.0.0.1:%d/md/spare" % PORT)

# --------------------------------------------------------------------------
# 第 10 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/mdm_spare.js              新增 571 行
#   [修改] templates/layout_mdm.html        新增 1 行
#   [新增] templates/panel_spare.html       新增 406 行
