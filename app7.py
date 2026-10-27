# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-27（第 7 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 7 天新增与修改的源代码，不含第 1~6 天已有代码
#           本次迭代主题：设备报废审批模块 + 报废审批页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5001/md/overview）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 7 天新增业务模块 1：mdm_scrap.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-27（第 7 天 / 共 15 天）
# 模块功能：设备报废审批模块。负责医疗器械报废申请发起、四级审批流转、
#           审批意见留痕、报废处置归档与台账状态联动，支持按申请编号、
#           设备名称、使用科室、审批状态、报废原因与申请日期区间组合筛选
#           并分页，输出报废原因分布、审批状态分布、科室报废分布、已用
#           年限分布与近 6 个月报废趋势等统计图表，并在处置归档时同步回
#           写设备台账状态为「已报废」、登记状态流转与系统操作流水，形成
#           「申请 → 鉴定 → 审批 → 核销 → 处置」的报废全生命周期闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from mdm_settings import ASSET_CATEGORY_DICT
from mdm_dal import get_connection, write_ops_journal, append_status_flow

# ------------------------------------------------------------------
# 报废审批节点定义：三级审批流转，顺序即审批推进顺序；三级全部通过后
# 申请进入「已批准报废」，再由处置归档动作完成报废流程的最后一步
# ------------------------------------------------------------------
AUDIT_NODE_LIST = [
    ("ENGINEER", "医工科技术鉴定"),
    ("LEADER", "院领导审批"),
    ("FINANCE", "财务核销"),
]

AUDIT_NODE_DICT = {
    "ENGINEER": "医工科技术鉴定",
    "LEADER": "院领导审批",
    "FINANCE": "财务核销",
}

# 审批状态字典：节点编码即当前停留节点，APPROVED / REJECTED / DISPOSED 为终态
AUDIT_STATE_DICT = {
    "ENGINEER": "待技术鉴定",
    "LEADER": "待院领导审批",
    "FINANCE": "待财务核销",
    "APPROVED": "已批准报废",
    "REJECTED": "已驳回",
    "DISPOSED": "已处置归档",
}

# 审批结论字典：通过推进到下一节点，驳回直接终结流程
AUDIT_RESULT_DICT = {
    "PASS": "同意",
    "REJECT": "驳回",
}

# 报废原因字典：按医疗器械报废常见情形划分
SCRAP_REASON_DICT = {
    "AGING": "达到使用年限",
    "FAULT": "故障无法修复",
    "COST": "维修成本过高",
    "TECH": "技术淘汰更新",
    "DAMAGE": "意外损坏",
    "OTHER": "其他原因",
}

# 报废处置方式字典：处置归档时选择，影响残值回收金额
DISPOSE_METHOD_DICT = {
    "SELL": "残值变卖",
    "RECYCLE": "环保回收",
    "DONATE": "调剂捐赠",
    "STORE": "封存留档",
}

# 单页默认条数与最大条数
SCRAP_PAGE_SIZE = 10
SCRAP_PAGE_MAX = 100

# 报废申请编号前缀，编号形如 SCR-2026-0001
SCRAP_CODE_PREFIX = "SCR"

# 直线折旧残值率下限：净值低于原值的该比例即按该比例计
SALVAGE_RATE = 0.05

# 达到或超过预计使用年限的比例即视为「超期服役」
OVERAGE_RATE = 1.0


def today_text():
    """返回当前日期文本，格式 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def shift_days_text(days):
    """返回相对今天偏移指定天数后的日期文本，负数表示往前推。"""
    return (datetime.date.today() + datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def month_tag(date_text):
    """取日期文本所在月份标签，格式 YYYY-MM（非法日期返回空串）。"""
    if not date_text:
        return ""
    return str(date_text)[:7]


def round2(value):
    """保留两位小数，空值与非数值按 0 处理。"""
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def calc_used_years(purchase_date):
    """按采购日期计算已用年限，保留一位小数（无采购日期返回 0）。"""
    if not purchase_date:
        return 0.0
    try:
        start = datetime.datetime.strptime(str(purchase_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return 0.0
    days = (datetime.date.today() - start).days
    return round(max(days, 0) / 365.0, 1)


def calc_net_value(purchase_price, service_life, used_years):
    """直线法计算账面净值：原值 × (1 - 已用年限 / 预计年限)，保底为残值率。"""
    price = round2(purchase_price)
    if price <= 0:
        return 0.0
    life = 0
    try:
        life = int(service_life)
    except (TypeError, ValueError):
        life = 0
    if life <= 0:
        return price
    ratio = 1.0 - (float(used_years) / float(life))
    if ratio < SALVAGE_RATE:
        ratio = SALVAGE_RATE
    if ratio > 1.0:
        ratio = 1.0
    return round2(price * ratio)


def judge_overage(service_life, used_years):
    """判断是否超期服役：已用年限达到预计使用年限即超期。"""
    try:
        life = int(service_life)
    except (TypeError, ValueError):
        return False
    if life <= 0:
        return False
    return float(used_years) >= float(life) * OVERAGE_RATE


def next_audit_node(state_code):
    """返回当前审批状态下一步应推进到的节点编码，终态返回空串。"""
    order = [node[0] for node in AUDIT_NODE_LIST]
    if state_code not in order:
        return ""
    index = order.index(state_code)
    if index + 1 >= len(order):
        return "APPROVED"
    return order[index + 1]


def fetch_asset_row(asset_code):
    """按资产编号读取台账完整记录，供报废申请带出设备档案。"""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM md_asset_ledger WHERE asset_code=?", (asset_code,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        columns = [item[0] for item in cursor.description]
        return dict(zip(columns, row))
    finally:
        conn.close()


def generate_scrap_code(conn):
    """生成报废申请编号，形如 SCR-2026-0001，按当年已有条数递增。"""
    year_text = datetime.date.today().strftime("%Y")
    row = conn.execute(
        "SELECT COUNT(1) AS amount FROM md_scrap_apply "
        "WHERE apply_code LIKE ?", ("%s-%s-%%" % (SCRAP_CODE_PREFIX, year_text),)
    ).fetchone()
    amount = row[0] if row else 0
    return "%s-%s-%04d" % (SCRAP_CODE_PREFIX, year_text, amount + 1)


# ==================================================================
# 报废申请提交
# ==================================================================
def submit_scrap_apply(payload):
    """提交一条报废申请，返回统一结果字典。

    校验点：资产编号必填、设备必须存在于台账、设备不能已报废或已提交在审、
    报废原因必须是字典内的编码、申请说明长度不得超过 200 字。
    """
    asset_code = (payload.get("asset_code") or "").strip()
    reason_code = (payload.get("reason_code") or "").strip()
    apply_person = (payload.get("apply_person") or "").strip()
    apply_note = (payload.get("apply_note") or "").strip()
    apply_date = (payload.get("apply_date") or "").strip() or today_text()

    if not asset_code:
        return {"success": False, "message": "资产编号不能为空"}
    if reason_code not in SCRAP_REASON_DICT:
        return {"success": False, "message": "报废原因编码不合法"}
    if not apply_person:
        return {"success": False, "message": "申请人不能为空"}
    if len(apply_note) > 200:
        return {"success": False, "message": "申请说明不得超过 200 字"}

    asset = fetch_asset_row(asset_code)
    if asset is None:
        return {"success": False, "message": "资产编号 %s 不存在于设备台账" % asset_code}
    if asset["current_status"] == "SCRAP":
        return {"success": False, "message": "该设备已报废，不可重复申请"}

    conn = get_connection()
    try:
        exist = conn.execute(
            "SELECT apply_code FROM md_scrap_apply WHERE asset_code=? "
            "AND audit_state NOT IN ('REJECTED','DISPOSED')", (asset_code,)
        ).fetchone()
        if exist is not None:
            return {"success": False, "message": "该设备已有在审或已批准的报废申请"}

        used_years = calc_used_years(asset["purchase_date"])
        net_value = calc_net_value(
            asset["purchase_price"], asset["service_life"], used_years)
        apply_code = generate_scrap_code(conn)
        conn.execute(
            "INSERT INTO md_scrap_apply (apply_code, asset_code, asset_name, "
            "dept_name, asset_category, purchase_date, purchase_price, "
            "service_life, used_years, net_value, reason_code, apply_note, "
            "apply_person, apply_date, audit_state, current_node) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (apply_code, asset_code, asset["asset_name"], asset["dept_name"],
             asset["asset_category"], asset["purchase_date"],
             round2(asset["purchase_price"]), asset["service_life"], used_years,
             net_value, reason_code, apply_note, apply_person, apply_date,
             "ENGINEER", "ENGINEER"),
        )
        conn.execute(
            "INSERT INTO md_scrap_audit (apply_code, step_no, node_code, "
            "node_name, auditor_name, audit_result, audit_opinion) "
            "VALUES (?,?,?,?,?,?,?)",
            (apply_code, 1, "ENGINEER", AUDIT_NODE_DICT["ENGINEER"],
             apply_person, "SUBMIT", "科室提交报废申请"),
        )
        conn.commit()
    finally:
        conn.close()

    write_ops_journal("报废审批", "提交申请", asset_code,
                      "%s 提交报废申请 %s，原因：%s"
                      % (apply_person, apply_code, SCRAP_REASON_DICT[reason_code]),
                      apply_person)
    return {"success": True, "message": "报废申请已提交，进入技术鉴定环节",
            "apply_code": apply_code, "net_value": net_value}


# ==================================================================
# 审批流转
# ==================================================================
def audit_scrap_apply(apply_code, result_code, opinion_text, auditor_name):
    """对一条报废申请做一次审批动作，通过则推进到下一节点，驳回则终结。"""
    apply_code = (apply_code or "").strip()
    opinion_text = (opinion_text or "").strip()
    auditor_name = (auditor_name or "").strip()

    if not apply_code:
        return {"success": False, "message": "申请编号不能为空"}
    if result_code not in AUDIT_RESULT_DICT:
        return {"success": False, "message": "审批结论编码不合法"}
    if not auditor_name:
        return {"success": False, "message": "审批人不能为空"}
    if result_code == "REJECT" and not opinion_text:
        return {"success": False, "message": "驳回时必须填写审批意见"}

    conn = get_connection()
    try:
        conn.row_factory = None
        cursor = conn.execute(
            "SELECT * FROM md_scrap_apply WHERE apply_code=?", (apply_code,)
        )
        row = cursor.fetchone()
        if row is None:
            return {"success": False, "message": "申请编号 %s 不存在" % apply_code}
        columns = [item[0] for item in cursor.description]
        apply_row = dict(zip(columns, row))
        state = apply_row["audit_state"]
        if state in ("REJECTED", "DISPOSED"):
            return {"success": False, "message": "该申请已终结，不可继续审批"}

        step_row = conn.execute(
            "SELECT COALESCE(MAX(step_no),0) AS max_step FROM md_scrap_audit "
            "WHERE apply_code=?", (apply_code,)
        ).fetchone()
        step_no = (step_row[0] if step_row else 0) + 1

        conn.execute(
            "INSERT INTO md_scrap_audit (apply_code, step_no, node_code, "
            "node_name, auditor_name, audit_result, audit_opinion, audit_time) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (apply_code, step_no, state, AUDIT_NODE_DICT.get(state, state),
             auditor_name, result_code, opinion_text or AUDIT_RESULT_DICT[result_code],
             datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        if result_code == "REJECT":
            new_state = "REJECTED"
        else:
            new_state = next_audit_node(state)
        conn.execute(
            "UPDATE md_scrap_apply SET audit_state=?, current_node=? "
            "WHERE apply_code=?", (new_state, new_state, apply_code),
        )
        conn.commit()
    finally:
        conn.close()

    write_ops_journal("报废审批", "审批流转", apply_row["asset_code"],
                      "%s 在「%s」节点%s：%s"
                      % (auditor_name, AUDIT_NODE_DICT.get(state, state),
                         AUDIT_RESULT_DICT[result_code],
                         opinion_text or "无意见"), auditor_name)
    return {"success": True,
            "message": "审批已记录，申请状态更新为「%s」"
                       % AUDIT_STATE_DICT.get(new_state, new_state),
            "audit_state": new_state}


# ==================================================================
# 报废处置归档
# ==================================================================
def dispose_scrap_apply(apply_code, method_code, dispose_amount, operator_name, note_text=""):
    """对已批准（APPROVED）的申请做处置归档，并回写台账状态为「已报废」。"""
    apply_code = (apply_code or "").strip()
    operator_name = (operator_name or "").strip()
    note_text = (note_text or "").strip()

    if not apply_code:
        return {"success": False, "message": "申请编号不能为空"}
    if method_code not in DISPOSE_METHOD_DICT:
        return {"success": False, "message": "处置方式编码不合法"}
    if not operator_name:
        return {"success": False, "message": "处置人不能为空"}
    try:
        amount = float(dispose_amount)
    except (TypeError, ValueError):
        return {"success": False, "message": "残值回收金额必须是数字"}
    if amount < 0:
        return {"success": False, "message": "残值回收金额不能为负数"}

    conn = get_connection()
    try:
        conn.row_factory = None
        cursor = conn.execute(
            "SELECT * FROM md_scrap_apply WHERE apply_code=?", (apply_code,)
        )
        row = cursor.fetchone()
        if row is None:
            return {"success": False, "message": "申请编号 %s 不存在" % apply_code}
        columns = [item[0] for item in cursor.description]
        apply_row = dict(zip(columns, row))
        if apply_row["audit_state"] != "APPROVED":
            return {"success": False,
                    "message": "仅「已批准报废」的申请可执行处置，当前状态：%s"
                               % AUDIT_STATE_DICT.get(apply_row["audit_state"], "")}

        finish_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE md_scrap_apply SET audit_state='DISPOSED', current_node='DISPOSED', "
            "dispose_method=?, dispose_amount=?, dispose_note=?, "
            "dispose_person=?, finish_at=? WHERE apply_code=?",
            (method_code, round2(amount), note_text, operator_name,
             finish_at, apply_code),
        )
        asset_row = conn.execute(
            "SELECT asset_id, current_status FROM md_asset_ledger WHERE asset_code=?",
            (apply_row["asset_code"],)
        ).fetchone()
        if asset_row is not None:
            conn.execute(
                "UPDATE md_asset_ledger SET current_status='SCRAP', "
                "updated_at=? WHERE asset_id=?",
                (finish_at, asset_row[0]),
            )
        conn.commit()
    finally:
        conn.close()

    if asset_row is not None:
        append_status_flow(asset_row[0], asset_row[1], "SCRAP",
                           "报废审批 %s 处置归档" % apply_code, operator_name)
    write_ops_journal("报废审批", "处置归档", apply_row["asset_code"],
                      "%s 完成报废处置（%s），残值回收 %.2f 元"
                      % (operator_name, DISPOSE_METHOD_DICT[method_code], amount),
                      operator_name)
    return {"success": True, "message": "报废处置已归档，台账状态同步为「已报废」",
            "dispose_amount": round2(amount)}


# ==================================================================
# 查询与统计
# ==================================================================
def build_scrap_condition(keyword="", dept_name="", audit_state="",
                          reason_code="", date_from="", date_to=""):
    """按筛选条件拼装 WHERE 子句与参数列表。"""
    clauses = []
    params = []
    if keyword:
        clauses.append("(apply_code LIKE ? OR asset_code LIKE ? OR asset_name LIKE ? "
                       "OR apply_person LIKE ?)")
        like_text = "%%%s%%" % keyword
        params.extend([like_text, like_text, like_text, like_text])
    if dept_name:
        clauses.append("dept_name = ?")
        params.append(dept_name)
    if audit_state:
        clauses.append("audit_state = ?")
        params.append(audit_state)
    if reason_code:
        clauses.append("reason_code = ?")
        params.append(reason_code)
    if date_from:
        clauses.append("apply_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("apply_date <= ?")
        params.append(date_to)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


def fetch_scrap_rows(keyword="", dept_name="", audit_state="",
                     reason_code="", date_from="", date_to="", page=1, size=SCRAP_PAGE_SIZE):
    """分页查询报废申请列表，附加中文标签与超期服役标记。"""
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    try:
        page_size = int(size)
    except (TypeError, ValueError):
        page_size = SCRAP_PAGE_SIZE
    page_no = max(page_no, 1)
    page_size = min(max(page_size, 1), SCRAP_PAGE_MAX)
    offset = (page_no - 1) * page_size

    where_sql, params = build_scrap_condition(
        keyword, dept_name, audit_state, reason_code, date_from, date_to)
    conn = get_connection()
    try:
        conn.row_factory = None
        total = conn.execute(
            "SELECT COUNT(1) FROM md_scrap_apply" + where_sql, params
        ).fetchone()[0]
        cursor = conn.execute(
            "SELECT * FROM md_scrap_apply" + where_sql +
            " ORDER BY apply_id DESC LIMIT ? OFFSET ?",
            params + [page_size, offset],
        )
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    finally:
        conn.close()

    records = []
    for row in rows:
        item = dict(zip(columns, row))
        item["state_label"] = AUDIT_STATE_DICT.get(item["audit_state"], "未知状态")
        item["reason_label"] = SCRAP_REASON_DICT.get(item["reason_code"], "未分类")
        item["category_label"] = ASSET_CATEGORY_DICT.get(item["asset_category"], "未分类")
        item["dispose_label"] = DISPOSE_METHOD_DICT.get(item["dispose_method"], "")
        item["overage_flag"] = judge_overage(item["service_life"], item["used_years"])
        records.append(item)
    total_page = (total + page_size - 1) // page_size if page_size else 1
    return records, total, total_page, page_no


def fetch_scrap_audit_trail(apply_code):
    """读取某条申请的审批流转轨迹，按步骤号升序返回。"""
    conn = get_connection()
    try:
        conn.row_factory = None
        cursor = conn.execute(
            "SELECT * FROM md_scrap_audit WHERE apply_code=? ORDER BY step_no ASC",
            (apply_code,),
        )
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
    finally:
        conn.close()
    result = []
    for row in rows:
        item = dict(zip(columns, row))
        item["result_label"] = AUDIT_RESULT_DICT.get(item["audit_result"], "提交")
        result.append(item)
    return result


def build_scrap_summary():
    """统计报废申请总量、在审量、已批准量、已处置量与账面净值合计。"""
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(1) FROM md_scrap_apply").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(1) FROM md_scrap_apply WHERE audit_state IN "
            "('ENGINEER','LEADER','FINANCE')").fetchone()[0]
        approved = conn.execute(
            "SELECT COUNT(1) FROM md_scrap_apply WHERE audit_state='APPROVED'"
        ).fetchone()[0]
        disposed = conn.execute(
            "SELECT COUNT(1) FROM md_scrap_apply WHERE audit_state='DISPOSED'"
        ).fetchone()[0]
        rejected = conn.execute(
            "SELECT COUNT(1) FROM md_scrap_apply WHERE audit_state='REJECTED'"
        ).fetchone()[0]
        net_value = conn.execute(
            "SELECT COALESCE(SUM(net_value),0) FROM md_scrap_apply "
            "WHERE audit_state IN ('APPROVED','DISPOSED')").fetchone()[0]
        recycle = conn.execute(
            "SELECT COALESCE(SUM(dispose_amount),0) FROM md_scrap_apply "
            "WHERE audit_state='DISPOSED'").fetchone()[0]
        original = conn.execute(
            "SELECT COALESCE(SUM(purchase_price),0) FROM md_scrap_apply").fetchone()[0]
        overage = conn.execute(
            "SELECT COUNT(1) FROM md_scrap_apply "
            "WHERE service_life>0 AND used_years>=service_life").fetchone()[0]
    finally:
        conn.close()
    return {
        "total": total,
        "pending": pending,
        "approved": approved,
        "disposed": disposed,
        "rejected": rejected,
        "net_value": round2(net_value),
        "recycle_amount": round2(recycle),
        "original_value": round2(original),
        "overage_amount": overage,
    }


def build_scrap_charts():
    """构建报废原因分布、状态分布、科室分布、年限分布与近 6 月趋势。"""
    conn = get_connection()
    try:
        reason_rows = conn.execute(
            "SELECT reason_code, COUNT(1) AS amount FROM md_scrap_apply "
            "GROUP BY reason_code").fetchall()
        state_rows = conn.execute(
            "SELECT audit_state, COUNT(1) AS amount FROM md_scrap_apply "
            "GROUP BY audit_state").fetchall()
        dept_rows = conn.execute(
            "SELECT dept_name, COUNT(1) AS amount, "
            "COALESCE(SUM(net_value),0) AS net_value FROM md_scrap_apply "
            "GROUP BY dept_name ORDER BY amount DESC LIMIT 8").fetchall()
        all_rows = conn.execute(
            "SELECT service_life, used_years FROM md_scrap_apply").fetchall()
        month_rows = conn.execute(
            "SELECT substr(apply_date,1,7) AS month_tag, COUNT(1) AS amount, "
            "COALESCE(SUM(net_value),0) AS net_value FROM md_scrap_apply "
            "GROUP BY month_tag ORDER BY month_tag DESC LIMIT 6").fetchall()
    finally:
        conn.close()

    reason_distribution = [
        {"name": SCRAP_REASON_DICT.get(row[0], "未分类"), "value": row[1]}
        for row in reason_rows
    ]
    state_distribution = [
        {"name": AUDIT_STATE_DICT.get(row[0], row[0]), "value": row[1]}
        for row in state_rows
    ]
    dept_distribution = [
        {"name": row[0] or "未分配", "value": row[1], "net_value": round2(row[2])}
        for row in dept_rows
    ]

    year_bucket = {"3 年以内": 0, "3-6 年": 0, "6-10 年": 0, "10 年以上": 0}
    for row in all_rows:
        used = float(row[1] or 0)
        if used < 3:
            year_bucket["3 年以内"] += 1
        elif used < 6:
            year_bucket["3-6 年"] += 1
        elif used < 10:
            year_bucket["6-10 年"] += 1
        else:
            year_bucket["10 年以上"] += 1
    year_distribution = [
        {"name": name, "value": amount} for name, amount in year_bucket.items()
    ]

    month_trend = [
        {"month": row[0], "amount": row[1], "net_value": round2(row[2])}
        for row in reversed(month_rows)
    ]
    return {
        "reason_distribution": reason_distribution,
        "state_distribution": state_distribution,
        "dept_distribution": dept_distribution,
        "year_distribution": year_distribution,
        "month_trend": month_trend,
    }


def build_scrap_payload(keyword="", dept_name="", audit_state="",
                        reason_code="", date_from="", date_to="", page=1, size=SCRAP_PAGE_SIZE):
    """组装报废审批页面所需的全部数据：汇总 + 图表 + 分页明细。"""
    records, total, total_page, page_no = fetch_scrap_rows(
        keyword, dept_name, audit_state, reason_code, date_from, date_to, page, size)
    return {
        "result": "OK",
        "summary": build_scrap_summary(),
        "charts": build_scrap_charts(),
        "records": records,
        "total": total,
        "total_page": total_page,
        "page": page_no,
        "options": build_scrap_options(),
    }


def build_scrap_options():
    """输出页面下拉框所需的字典选项与可申请报废的设备清单。"""
    conn = get_connection()
    try:
        dept_rows = conn.execute(
            "SELECT DISTINCT dept_name FROM md_asset_ledger "
            "WHERE dept_name<>'' ORDER BY dept_name").fetchall()
        asset_rows = conn.execute(
            "SELECT asset_code, asset_name, dept_name, current_status "
            "FROM md_asset_ledger WHERE current_status<>'SCRAP' "
            "ORDER BY asset_code").fetchall()
    finally:
        conn.close()
    return {
        "audit_state_dict": AUDIT_STATE_DICT,
        "reason_dict": SCRAP_REASON_DICT,
        "dispose_dict": DISPOSE_METHOD_DICT,
        "node_dict": AUDIT_NODE_DICT,
        "dept_list": [row[0] for row in dept_rows],
        "asset_list": [
            {"asset_code": row[0], "asset_name": row[1], "dept_name": row[2],
             "current_status": row[3]}
            for row in asset_rows
        ],
    }


def build_scrap_brief():
    """报废审批看板摘要，供运维总览看板嵌入。"""
    summary = build_scrap_summary()
    return {
        "total": summary["total"],
        "pending": summary["pending"],
        "disposed": summary["disposed"],
        "net_value": summary["net_value"],
        "text": "在审 %d 台 / 已处置 %d 台" % (summary["pending"], summary["disposed"]),
    }


# ==================================================================
# 演示数据
# ==================================================================
def seed_demo_scrap():
    """灌入演示报废申请：覆盖各审批节点与报废原因，首日启动即可看到效果。"""
    conn = get_connection()
    try:
        if conn.execute("SELECT COUNT(1) FROM md_scrap_apply").fetchone()[0] > 0:
            return 0
        conn.row_factory = None
        cursor = conn.execute(
            "SELECT asset_code, asset_name, dept_name, asset_category, "
            "purchase_date, purchase_price, service_life FROM md_asset_ledger "
            "ORDER BY asset_id")
        rows = cursor.fetchall()
    finally:
        conn.close()
    if not rows:
        return 0

    # 优先选取已用年限占预计年限比例最高的设备，使演示数据更贴近真实报废场景
    def used_ratio(row):
        life = row[6] if row[6] else 0
        if not life:
            return 0.0
        return calc_used_years(row[4]) / float(life)

    rows = sorted(rows, key=used_ratio, reverse=True)[:6]

    reason_plan = ["AGING", "FAULT", "COST", "TECH", "DAMAGE", "OTHER"]
    amount = 0
    for index, row in enumerate(rows[:6]):
        asset_code, asset_name, dept_name, category = row[0], row[1], row[2], row[3]
        purchase_date, purchase_price, service_life = row[4], row[5], row[6]
        used_years = calc_used_years(purchase_date)
        net_value = calc_net_value(purchase_price, service_life, used_years)
        payload = {
            "asset_code": asset_code,
            "reason_code": reason_plan[index % len(reason_plan)],
            "apply_person": ["张伟", "李静", "王强", "刘敏", "陈涛", "赵磊"][index % 6],
            "apply_note": ["设备老化精度下降", "主板故障维修无果",
                           "维修报价超过净值", "厂家已停止配件供应",
                           "搬运过程意外损坏", "科室调整不再使用"][index % 6],
            "apply_date": shift_days_text(-3 * (index + 1)),
        }
        result = submit_scrap_apply(payload)
        if not result.get("success"):
            continue
        amount += 1
        apply_code = result["apply_code"]

        if index % 6 == 0:
            # 第 1 台：走完三级审批并处置归档，形成完整闭环样例
            audit_scrap_apply(apply_code, "PASS", "技术鉴定确认无修复价值", "医工科-周工")
            audit_scrap_apply(apply_code, "PASS", "同意报废", "院办-孙院长")
            audit_scrap_apply(apply_code, "PASS", "财务核销完毕", "财务科-吴会计")
            dispose_scrap_apply(apply_code, "RECYCLE", round2(net_value * 0.08),
                                "后勤-郑师傅", "交由有资质单位环保回收")
        elif index % 6 == 1:
            # 第 2 台：三级审批全部通过，停在「已批准报废」待处置
            audit_scrap_apply(apply_code, "PASS", "技术鉴定确认无修复价值", "医工科-周工")
            audit_scrap_apply(apply_code, "PASS", "同意报废", "院办-孙院长")
            audit_scrap_apply(apply_code, "PASS", "财务核销完毕", "财务科-吴会计")
        elif index % 6 == 2:
            # 第 3 台：技术鉴定通过，停在院领导审批
            audit_scrap_apply(apply_code, "PASS", "技术鉴定确认无修复价值", "医工科-周工")
        elif index % 6 == 3:
            # 第 4 台：停在技术鉴定
            pass
        elif index % 6 == 4:
            # 第 5 台：技术鉴定环节被驳回
            audit_scrap_apply(apply_code, "REJECT", "仍可修复使用，暂不同意报废",
                              "医工科-周工")
        else:
            # 第 6 台：走到财务核销
            audit_scrap_apply(apply_code, "PASS", "技术鉴定确认无修复价值", "医工科-周工")
            audit_scrap_apply(apply_code, "PASS", "同意报废，优先更新替代", "院办-孙院长")
    return amount

# --------------------------------------------------------------------------
# 第 7 天数据表脚本增量：mdm_schema.sql（M，已内联为字符串常量 DAY7_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY7_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 7 天新增：设备报废申请表
-- ==================================================================
-- 报废申请表：按「一台设备一次申请」记录报废发起信息，带出设备档案快照
-- 与直线法计算的账面净值，并保存当前审批停留节点与处置结果，支撑报废
-- 全生命周期「申请 → 鉴定 → 审批 → 核销 → 处置」闭环
CREATE TABLE IF NOT EXISTS md_scrap_apply (
    apply_id       INTEGER PRIMARY KEY AUTOINCREMENT,  -- 申请主键
    apply_code     TEXT    DEFAULT '',                 -- 申请编号（SCR-年份-序号）
    asset_code     TEXT    NOT NULL,                   -- 资产编号
    asset_name     TEXT    DEFAULT '',                 -- 设备名称快照
    dept_name      TEXT    DEFAULT '',                 -- 使用科室快照
    asset_category TEXT    DEFAULT '',                 -- 设备分类快照
    purchase_date  TEXT    DEFAULT '',                 -- 采购日期快照
    purchase_price REAL    DEFAULT 0,                  -- 采购原值（元）
    service_life   INTEGER DEFAULT 0,                  -- 预计使用年限（年）
    used_years     REAL    DEFAULT 0,                  -- 已用年限（年）
    net_value      REAL    DEFAULT 0,                  -- 账面净值（元）
    reason_code    TEXT    DEFAULT 'AGING',            -- 报废原因（见 SCRAP_REASON_DICT）
    apply_note     TEXT    DEFAULT '',                 -- 申请说明
    apply_person   TEXT    DEFAULT '',                 -- 申请人
    apply_date     TEXT    DEFAULT '',                 -- 申请日期
    audit_state    TEXT    DEFAULT 'ENGINEER',         -- 审批状态（见 AUDIT_STATE_DICT）
    current_node   TEXT    DEFAULT 'ENGINEER',         -- 当前停留审批节点
    dispose_method TEXT    DEFAULT '',                 -- 处置方式（见 DISPOSE_METHOD_DICT）
    dispose_amount REAL    DEFAULT 0,                  -- 残值回收金额（元）
    dispose_note   TEXT    DEFAULT '',                 -- 处置说明
    dispose_person TEXT    DEFAULT '',                 -- 处置人
    finish_at      TEXT    DEFAULT '',                 -- 流程终结时间
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 7 天新增：报废审批流转表
-- ==================================================================
-- 审批流转表：按「一次申请一个节点一条」记录审批轨迹，含节点名称、审批人、
-- 审批结论、审批意见与审批时间，使报废审批全过程可追溯、责任可界定
CREATE TABLE IF NOT EXISTS md_scrap_audit (
    audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,   -- 流转主键
    apply_code   TEXT    NOT NULL,                    -- 关联申请编号
    step_no      INTEGER DEFAULT 0,                   -- 步骤序号（从 1 递增）
    node_code    TEXT    DEFAULT '',                  -- 审批节点编码
    node_name    TEXT    DEFAULT '',                  -- 审批节点名称
    auditor_name TEXT    DEFAULT '',                  -- 审批人
    audit_result TEXT    DEFAULT 'SUBMIT',            -- 审批结论（见 AUDIT_RESULT_DICT）
    audit_opinion TEXT   DEFAULT '',                  -- 审批意见
    audit_time   TEXT    DEFAULT ''                   -- 审批时间
);

CREATE INDEX IF NOT EXISTS idx_scrap_code   ON md_scrap_apply (apply_code);
CREATE INDEX IF NOT EXISTS idx_scrap_asset  ON md_scrap_apply (asset_code);
CREATE INDEX IF NOT EXISTS idx_scrap_state  ON md_scrap_apply (audit_state);
CREATE INDEX IF NOT EXISTS idx_scrap_dept   ON md_scrap_apply (dept_name);
CREATE INDEX IF NOT EXISTS idx_scrap_date   ON md_scrap_apply (apply_date);
CREATE INDEX IF NOT EXISTS idx_scrapaudit_code ON md_scrap_audit (apply_code);
"""


def apply_day7_schema(conn):
    """把第 7 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY7_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 7 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-27（第 7 天 / 共 15 天）
#           分析、盘点维保管理、运维日志查询与设备报废审批七套页面路由，
#           并提供台账增删改查、闲置识别扫描、状态切换、ROI 测算与年度
#           收支档案维护、盘点任务生成与账实核对、维保登记、运维日志检索
#           登记闭环与 CSV 导出，以及报废申请发起、逐级审批流转、处置归档
#           与审批轨迹查询等 RESTful 接口，供前端表格、弹窗与 ECharts 图表
#           异步取数

from mdm_scrap import (
    build_scrap_payload,
    build_scrap_brief,
    submit_scrap_apply,
    audit_scrap_apply,
    dispose_scrap_apply,
    fetch_scrap_audit_trail,
    seed_demo_scrap,
    AUDIT_STATE_DICT,
    SCRAP_REASON_DICT,
)

# 【函数内插入】def api_overview():
#    payload["scrap_brief"] = build_scrap_brief()

# ==================================================================
# 以下为第 7 天新增：设备报废审批页面与接口
# ==================================================================


@app.route("/md/scrap")
def panel_scrap():
    """设备报废审批页面：报废概览、五类分析图表、申请明细与审批流转。"""
    write_ops_journal("报废审批", "查询", "PANEL-SCRAP", "打开设备报废审批页面")
    return render_template(
        "panel_scrap.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/md/scrap/panel")
def api_scrap_panel():
    """报废审批数据接口：汇总指标、五类图表、分页明细与下拉选项。"""
    payload = build_scrap_payload(
        keyword=(request.args.get("keyword") or "").strip(),
        dept_name=(request.args.get("dept_name") or "").strip(),
        audit_state=(request.args.get("audit_state") or "").strip(),
        reason_code=(request.args.get("reason_code") or "").strip(),
        date_from=(request.args.get("date_from") or "").strip(),
        date_to=(request.args.get("date_to") or "").strip(),
        page=int(request.args.get("page", 1)),
    )
    return jsonify(payload)


@app.route("/api/md/scrap/apply", methods=["POST"])
def api_scrap_apply():
    """提交一条设备报废申请，进入医工科技术鉴定环节。"""
    body = request.get_json(silent=True) or {}
    result = submit_scrap_apply(body)
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/scrap/audit", methods=["POST"])
def api_scrap_audit():
    """对报废申请做一次审批动作，同意则推进到下一节点，驳回则终结流程。"""
    body = request.get_json(silent=True) or {}
    result = audit_scrap_apply(
        (body.get("apply_code") or "").strip(),
        (body.get("result_code") or "").strip(),
        (body.get("opinion_text") or "").strip(),
        (body.get("auditor_name") or "").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/scrap/dispose", methods=["POST"])
def api_scrap_dispose():
    """对已批准的报废申请执行处置归档，并回写台账状态为已报废。"""
    body = request.get_json(silent=True) or {}
    result = dispose_scrap_apply(
        (body.get("apply_code") or "").strip(),
        (body.get("method_code") or "").strip(),
        body.get("dispose_amount", 0),
        (body.get("operator_name") or "").strip(),
        (body.get("note_text") or "").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/scrap/trail")
def api_scrap_trail():
    """查询某条报废申请的逐级审批流转轨迹。"""
    return jsonify({
        "result": "OK",
        "records": fetch_scrap_audit_trail((request.args.get("apply_code") or "").strip()),
    })


@app.route("/api/md/scrap/options")
def api_scrap_options():
    """报废审批下拉选项：审批状态、报废原因与处置方式字典。"""
    return jsonify({
        "result": "OK",
        "state_options": [{"code": k, "label": v} for k, v in AUDIT_STATE_DICT.items()],
        "reason_options": [{"code": k, "label": v} for k, v in SCRAP_REASON_DICT.items()],
    })



# 【函数内插入】def bootstrap_application():
#    seed_demo_scrap()

# 【函数内插入】if __name__ == "__main__":
#    print("报废审批： http://127.0.0.1:%d/md/scrap" % PORT)

# --------------------------------------------------------------------------
# 第 7 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/mdm_scrap.js              新增 457 行
#   [修改] templates/layout_mdm.html        新增 1 行
#   [新增] templates/panel_scrap.html       新增 301 行
