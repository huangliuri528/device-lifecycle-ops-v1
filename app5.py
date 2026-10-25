# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-25（第 5 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 5 天新增与修改的源代码，不含第 1~4 天已有代码
#           本次迭代主题：设备盘点与维保记录管理模块 + 盘点维保页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5001/md/overview）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 5 天新增业务模块 1：mdm_inventory.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-25（第 5 天 / 共 15 天）
# 模块功能：设备盘点与维保记录管理模块。负责盘点任务按科室/分类范围批量
#           生成、盘点明细的账实核对与差异登记、盘点完成率与差异汇总，
#           以及维保到期预警分级、维保记录登记归档、维保闭环后自动滚动
#           下一次维保日期，并输出盘点进度、账实差异分布、维保到期分布、
#           维保类型占比与月度维保趋势等图表数据
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime
from mdm_settings import (
    ASSET_STATUS_DICT,
    ASSET_CATEGORY_DICT,
    RISK_LEVEL_DICT,
)
from mdm_dal import get_connection, write_ops_journal

# 盘点任务状态字典：覆盖从计划到归档的完整盘点流程
INVENTORY_TASK_STATUS = {
    "PLAN": "待盘点",
    "DOING": "盘点中",
    "DONE": "已完成",
    "CLOSED": "已归档",
}

# 账实核对结果字典：一次盘点明细只允许落一种结果
CHECK_RESULT_DICT = {
    "PENDING": "未盘点",
    "NORMAL": "账实相符",
    "LOSS": "盘亏",
    "PROFIT": "盘盈",
    "MOVE": "位置不符",
}

# 维保类型字典：区分日常保养与故障维修，便于费用归集
MAINTAIN_TYPE_DICT = {
    "ROUTINE": "日常保养",
    "PREVENT": "预防性维护",
    "REPAIR": "故障维修",
    "CALIB": "计量校准",
}

# 维保结论字典：不合格的维保需要复检，不滚动维保周期
MAINTAIN_RESULT_DICT = {
    "PASS": "合格",
    "FAIL": "不合格待复检",
}

# 维保到期等级字典：按距下次维保日期的剩余天数划分
DUE_LEVEL_DICT = {
    "OVERDUE": "已逾期",
    "URGENT": "7天内到期",
    "SOON": "30天内到期",
    "NORMAL": "周期正常",
}

# 维保到期分级边界（天）：负数即已逾期，0~7 天加急，8~30 天临近
URGENT_DUE_FLOOR = 0
URGENT_DUE_CEIL = 7
SOON_DUE_CEIL = 30

# 台账未配置维保周期时使用的兜底周期（天）
DEFAULT_MAINTAIN_CYCLE = 180


def today_text():
    """返回当前日期文本，格式 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def parse_date_text(date_text):
    """把日期文本解析为 date 对象，非法或为空时返回 None。"""
    if not date_text:
        return None
    try:
        return datetime.datetime.strptime(str(date_text)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def calc_due_days(next_maintain_at):
    """计算距下次维保日期的剩余天数，为负表示已逾期。

    下次维保日期为空时返回 None，由调用方按「未排期」单独归类。
    """
    target = parse_date_text(next_maintain_at)
    if target is None:
        return None
    return (target - datetime.date.today()).days


def judge_due_level(due_days):
    """按剩余天数匹配维保到期等级编码与名称。

    判定口径：剩余天数为负 → 已逾期；0~7 天 → 7天内到期；
    8~30 天 → 30天内到期；超过 30 天或未排期 → 周期正常。
    """
    if due_days is None:
        return "NORMAL", DUE_LEVEL_DICT["NORMAL"]
    if due_days < URGENT_DUE_FLOOR:
        return "OVERDUE", DUE_LEVEL_DICT["OVERDUE"]
    if due_days <= URGENT_DUE_CEIL:
        return "URGENT", DUE_LEVEL_DICT["URGENT"]
    if due_days <= SOON_DUE_CEIL:
        return "SOON", DUE_LEVEL_DICT["SOON"]
    return "NORMAL", DUE_LEVEL_DICT["NORMAL"]


def build_maintain_plan_rows():
    """生成维保到期清单：逐台设备计算剩余天数与到期等级。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT asset_id, asset_code, asset_name, asset_category, dept_name, "
            "location_text, maintain_cycle, last_maintain_at, next_maintain_at, "
            "custodian, current_status FROM md_asset_ledger "
            "WHERE current_status NOT IN ('SCRAP') ORDER BY next_maintain_at, asset_id"
        ).fetchall()
    finally:
        conn.close()

    plan_rows = []
    for item in [dict(r) for r in rows]:
        due_days = calc_due_days(item["next_maintain_at"])
        level_code, level_name = judge_due_level(due_days)
        cycle = int(item["maintain_cycle"] or 0) or DEFAULT_MAINTAIN_CYCLE
        plan_rows.append({
            "asset_id": item["asset_id"],
            "asset_code": item["asset_code"],
            "asset_name": item["asset_name"],
            "category_label": ASSET_CATEGORY_DICT.get(item["asset_category"], "其他"),
            "dept_name": item["dept_name"] or "未分配",
            "location_text": item["location_text"],
            "status_label": ASSET_STATUS_DICT.get(item["current_status"], "未知"),
            "maintain_cycle": cycle,
            "last_maintain_at": item["last_maintain_at"] or "",
            "next_maintain_at": item["next_maintain_at"] or "",
            "custodian": item["custodian"] or "",
            "due_days": due_days if due_days is not None else 9999,
            "due_text": ("未排期" if due_days is None
                         else ("已逾期 %d 天" % abs(due_days) if due_days < 0
                               else "剩余 %d 天" % due_days)),
            "level_code": level_code,
            "level_name": level_name,
        })
    # 逾期设备排在最前，便于管理员优先处理
    return sorted(plan_rows, key=lambda x: x["due_days"])


def summarize_maintain_plan(plan_rows):
    """汇总维保到期情况：各等级台数、本月应维保与年度累计费用。"""
    level_counter = {code: 0 for code in DUE_LEVEL_DICT}
    for row in plan_rows:
        level_counter[row["level_code"]] = level_counter.get(row["level_code"], 0) + 1

    conn = get_connection()
    try:
        fee_row = conn.execute(
            "SELECT COUNT(1) AS amount, COALESCE(SUM(maintain_cost), 0) AS cost "
            "FROM md_maintain_record WHERE substr(maintain_date, 1, 4)=?",
            (str(datetime.date.today().year),),
        ).fetchone()
    finally:
        conn.close()

    return {
        "plan_amount": len(plan_rows),
        "overdue_amount": level_counter.get("OVERDUE", 0),
        "urgent_amount": level_counter.get("URGENT", 0),
        "soon_amount": level_counter.get("SOON", 0),
        "normal_amount": level_counter.get("NORMAL", 0),
        "year_amount": fee_row["amount"] if fee_row else 0,
        "year_cost": round(float(fee_row["cost"]) if fee_row else 0.0, 2),
    }


def fetch_maintain_records(asset_code="", limit=50):
    """查询维保记录，可按设备编号过滤，按维保日期倒序返回。"""
    conn = get_connection()
    try:
        if asset_code:
            rows = conn.execute(
                "SELECT record_id, asset_code, asset_name, maintain_type, maintain_date, "
                "vendor_text, engineer_name, content_text, maintain_cost, result_code, "
                "next_maintain_at FROM md_maintain_record WHERE asset_code=? "
                "ORDER BY maintain_date DESC, record_id DESC LIMIT ?",
                (asset_code, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT record_id, asset_code, asset_name, maintain_type, maintain_date, "
                "vendor_text, engineer_name, content_text, maintain_cost, result_code, "
                "next_maintain_at FROM md_maintain_record "
                "ORDER BY maintain_date DESC, record_id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
    finally:
        conn.close()
    result = []
    for item in [dict(r) for r in rows]:
        item["type_label"] = MAINTAIN_TYPE_DICT.get(item["maintain_type"], "其他")
        item["result_label"] = MAINTAIN_RESULT_DICT.get(item["result_code"], "未判定")
        result.append(item)
    return result


def save_maintain_record(asset_code, maintain_type, maintain_date, vendor_text,
                         engineer_name, content_text, maintain_cost, result_code):
    """登记一条维保记录，并在结论合格后滚动台账的下一次维保日期。

    维保闭环逻辑：以本次维保日期为基线，按台账维保周期推算下次维保日期；
    结论为「不合格待复检」时不滚动周期，避免隐患设备被自动延期。
    """
    if not asset_code:
        return {"success": False, "message": "资产编号不能为空"}
    if maintain_type not in MAINTAIN_TYPE_DICT:
        return {"success": False, "message": "维保类型不合法：%s" % maintain_type}
    if result_code not in MAINTAIN_RESULT_DICT:
        return {"success": False, "message": "维保结论不合法：%s" % result_code}
    work_date = parse_date_text(maintain_date) or datetime.date.today()
    try:
        cost = round(float(maintain_cost or 0), 2)
    except (ValueError, TypeError):
        return {"success": False, "message": "维保费用必须为数字"}
    if cost < 0:
        return {"success": False, "message": "维保费用不能为负数"}

    conn = get_connection()
    try:
        asset = conn.execute(
            "SELECT asset_id, asset_code, asset_name, dept_name, maintain_cycle "
            "FROM md_asset_ledger WHERE asset_code=?", (asset_code,)
        ).fetchone()
        if asset is None:
            return {"success": False, "message": "资产编号不存在：%s" % asset_code}

        cycle = int(asset["maintain_cycle"] or 0) or DEFAULT_MAINTAIN_CYCLE
        next_date = ""
        if result_code == "PASS":
            next_date = (work_date + datetime.timedelta(days=cycle)).strftime("%Y-%m-%d")

        conn.execute(
            "INSERT INTO md_maintain_record (asset_code, asset_name, dept_name, "
            "maintain_type, maintain_date, vendor_text, engineer_name, content_text, "
            "maintain_cost, result_code, next_maintain_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (asset_code, asset["asset_name"], asset["dept_name"], maintain_type,
             work_date.strftime("%Y-%m-%d"), (vendor_text or "").strip(),
             (engineer_name or "").strip(), (content_text or "").strip(),
             cost, result_code, next_date),
        )
        if next_date:
            conn.execute(
                "UPDATE md_asset_ledger SET last_maintain_at=?, next_maintain_at=?, "
                "updated_at=datetime('now','localtime') WHERE asset_code=?",
                (work_date.strftime("%Y-%m-%d"), next_date, asset_code),
            )
        conn.commit()
    finally:
        conn.close()

    write_ops_journal("盘点维保", "新增", asset_code,
                      "登记%s记录：%s · 费用%.2f元 · 结论%s"
                      % (MAINTAIN_TYPE_DICT[maintain_type],
                         work_date.strftime("%Y-%m-%d"), cost,
                         MAINTAIN_RESULT_DICT[result_code]))
    return {
        "success": True,
        "message": "维保记录已登记" + ("，下次维保日期滚动至 %s" % next_date if next_date
                                  else "，结论不合格，维保周期不滚动"),
        "next_maintain_at": next_date,
    }


def build_maintain_charts(plan_rows):
    """组织维保分析图表：到期等级分布、维保类型占比、近 6 个月维保次数。"""
    due_rows = []
    for code, name in DUE_LEVEL_DICT.items():
        due_rows.append({
            "name": name,
            "value": sum(1 for r in plan_rows if r["level_code"] == code),
        })

    conn = get_connection()
    try:
        type_rows = conn.execute(
            "SELECT maintain_type, COUNT(1) AS amount FROM md_maintain_record "
            "GROUP BY maintain_type"
        ).fetchall()
        month_rows = conn.execute(
            "SELECT substr(maintain_date, 1, 7) AS month_tag, COUNT(1) AS amount, "
            "COALESCE(SUM(maintain_cost), 0) AS cost FROM md_maintain_record "
            "GROUP BY month_tag ORDER BY month_tag DESC LIMIT 6"
        ).fetchall()
    finally:
        conn.close()

    type_distribution = [
        {"name": MAINTAIN_TYPE_DICT.get(r["maintain_type"], "其他"),
         "value": r["amount"]}
        for r in type_rows
    ]
    months = [dict(r) for r in month_rows]
    months.reverse()
    return {
        "due_distribution": due_rows,
        "type_distribution": type_distribution,
        "month_trend": {
            "months": [m["month_tag"] for m in months],
            "amounts": [m["amount"] for m in months],
            "costs": [round(float(m["cost"]), 2) for m in months],
        },
    }


def build_maintain_payload(asset_code="", limit=50):
    """组装维保管理页数据：到期清单、汇总、记录、图表与下拉选项。"""
    plan_rows = build_maintain_plan_rows()
    return {
        "plan_rows": plan_rows,
        "summary": summarize_maintain_plan(plan_rows),
        "records": fetch_maintain_records(asset_code, limit),
        "charts": build_maintain_charts(plan_rows),
        "type_options": [{"code": k, "label": v} for k, v in MAINTAIN_TYPE_DICT.items()],
        "result_options": [{"code": k, "label": v} for k, v in MAINTAIN_RESULT_DICT.items()],
        "due_options": [{"code": k, "label": v} for k, v in DUE_LEVEL_DICT.items()],
    }


def generate_task_code():
    """按日期与序号生成盘点任务编号，格式 PD-YYYYMMDD-序号。"""
    day_tag = datetime.date.today().strftime("%Y%m%d")
    prefix = "PD-%s-" % day_tag
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(1) AS cnt FROM md_inventory_task WHERE task_code LIKE ?",
            (prefix + "%",),
        ).fetchone()
    finally:
        conn.close()
    return prefix + str(int(row["cnt"]) + 1).zfill(2)


def create_inventory_task(task_name, dept_name="", category_code="",
                          plan_start="", plan_end="", operator="admin"):
    """创建盘点任务，并按范围一次性生成待盘点明细。

    明细生成规则：把范围内每台设备的账面状态与存放位置写入明细作为核对
    基准，实盘状态留空等待盘点人员逐台录入。
    """
    conn = get_connection()
    try:
        where_parts = ["current_status NOT IN ('SCRAP')"]
        params = []
        if dept_name:
            where_parts.append("dept_name=?")
            params.append(dept_name)
        if category_code:
            where_parts.append("asset_category=?")
            params.append(category_code)
        assets = conn.execute(
            "SELECT asset_id, asset_code, asset_name, dept_name, location_text, "
            "current_status FROM md_asset_ledger WHERE " + " AND ".join(where_parts) +
            " ORDER BY dept_name, asset_id", params
        ).fetchall()
        if not assets:
            return {"success": False, "message": "所选范围内没有可盘点设备"}

        task_code = generate_task_code()
        cur = conn.execute(
            "INSERT INTO md_inventory_task (task_code, task_name, dept_scope, "
            "category_scope, plan_start, plan_end, task_status, expect_amount, "
            "checked_amount, diff_amount, operator_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (task_code, (task_name or "").strip() or "设备定期盘点", dept_name,
             category_code, plan_start or today_text(), plan_end or today_text(),
             "DOING", len(assets), 0, 0, operator),
        )
        task_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO md_inventory_record (task_id, asset_id, asset_code, asset_name, "
            "dept_name, book_status, book_location, check_result, location_match, "
            "remark_text) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(task_id, a["asset_id"], a["asset_code"], a["asset_name"], a["dept_name"],
              a["current_status"], a["location_text"], "PENDING", 1, "")
             for a in assets],
        )
        conn.commit()
    finally:
        conn.close()

    write_ops_journal("盘点维保", "新增", task_code,
                      "创建盘点任务：%s，生成待盘点明细 %d 条"
                      % ((task_name or "设备定期盘点"), len(assets)))
    return {
        "success": True,
        "message": "盘点任务已创建，生成待盘点明细 %d 条" % len(assets),
        "task_id": task_id,
        "task_code": task_code,
    }


def fetch_inventory_tasks(limit=20):
    """查询盘点任务列表，按创建时间倒序返回。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT task_id, task_code, task_name, dept_scope, category_scope, "
            "plan_start, plan_end, task_status, expect_amount, checked_amount, "
            "diff_amount, operator_name, created_at FROM md_inventory_task "
            "ORDER BY task_id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    finally:
        conn.close()
    result = []
    for item in [dict(r) for r in rows]:
        item["status_label"] = INVENTORY_TASK_STATUS.get(item["task_status"], "未知")
        item["scope_text"] = (item["dept_scope"] or "全部科室") + " / " + \
                             (ASSET_CATEGORY_DICT.get(item["category_scope"], "全部分类")
                              if item["category_scope"] else "全部分类")
        item["progress"] = round(item["checked_amount"] / item["expect_amount"] * 100, 2) \
            if item["expect_amount"] else 0.0
        result.append(item)
    return result


def fetch_inventory_records(task_id=0, keyword="", check_result=""):
    """查询某盘点任务的明细，支持按关键字与核对结果过滤。"""
    if not task_id:
        return []
    where_parts = ["r.task_id=?"]
    params = [int(task_id)]
    if keyword:
        where_parts.append("(r.asset_code LIKE ? OR r.asset_name LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like])
    if check_result:
        where_parts.append("r.check_result=?")
        params.append(check_result)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT r.record_id, r.task_id, r.asset_id, r.asset_code, r.asset_name, "
            "r.dept_name, r.book_status, r.book_location, r.actual_status, r.check_result, "
            "r.location_match, r.remark_text, r.checker_name, r.check_time "
            "FROM md_inventory_record r WHERE " + " AND ".join(where_parts) +
            " ORDER BY r.record_id", params
        ).fetchall()
    finally:
        conn.close()
    result = []
    for item in [dict(r) for r in rows]:
        item["book_label"] = ASSET_STATUS_DICT.get(item["book_status"], "未知")
        item["actual_label"] = ASSET_STATUS_DICT.get(item["actual_status"], "") \
            if item["actual_status"] else ""
        item["check_label"] = CHECK_RESULT_DICT.get(item["check_result"], "未盘点")
        result.append(item)
    return result


def save_inventory_check(record_id, check_result, actual_status="",
                         location_match=1, remark_text="", checker_name="admin"):
    """录入单条盘点结果，并回写任务的实盘数与差异数。"""
    if check_result not in CHECK_RESULT_DICT or check_result == "PENDING":
        return {"success": False, "message": "账实核对结果不合法：%s" % check_result}

    conn = get_connection()
    try:
        record = conn.execute(
            "SELECT record_id, task_id, asset_code, asset_name, check_result "
            "FROM md_inventory_record WHERE record_id=?", (int(record_id),)
        ).fetchone()
        if record is None:
            return {"success": False, "message": "盘点明细不存在：%s" % record_id}

        conn.execute(
            "UPDATE md_inventory_record SET actual_status=?, check_result=?, "
            "location_match=?, remark_text=?, checker_name=?, "
            "check_time=datetime('now','localtime') WHERE record_id=?",
            ((actual_status or "").strip(), check_result,
             int(location_match), (remark_text or "").strip(), checker_name, int(record_id)),
        )
        task_id = record["task_id"]
        counter = conn.execute(
            "SELECT SUM(CASE WHEN check_result<>'PENDING' THEN 1 ELSE 0 END) AS checked, "
            "SUM(CASE WHEN check_result<>'PENDING' AND check_result<>'NORMAL' THEN 1 ELSE 0 END) AS diff "
            "FROM md_inventory_record WHERE task_id=?", (task_id,)
        ).fetchone()
        conn.execute(
            "UPDATE md_inventory_task SET checked_amount=?, diff_amount=?, "
            "task_status=CASE WHEN ?>=expect_amount THEN 'DONE' ELSE 'DOING' END, "
            "updated_at=datetime('now','localtime') WHERE task_id=?",
            (int(counter["checked"]), int(counter["diff"] or 0),
             int(counter["checked"]), task_id),
        )
        conn.commit()
    finally:
        conn.close()

    write_ops_journal("盘点维保", "修改", record["asset_code"],
                      "盘点登记：%s（%s）" % (record["asset_name"],
                                        CHECK_RESULT_DICT[check_result]))
    return {"success": True, "message": "盘点结果已登记：%s" % CHECK_RESULT_DICT[check_result]}


def summarize_inventory_task(task_id):
    """汇总某盘点任务的完成情况与差异构成。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(1) AS expect, "
            "SUM(CASE WHEN check_result<>'PENDING' THEN 1 ELSE 0 END) AS checked, "
            "SUM(CASE WHEN check_result='NORMAL' THEN 1 ELSE 0 END) AS normal, "
            "SUM(CASE WHEN check_result='LOSS' THEN 1 ELSE 0 END) AS loss, "
            "SUM(CASE WHEN check_result='PROFIT' THEN 1 ELSE 0 END) AS profit, "
            "SUM(CASE WHEN check_result='MOVE' THEN 1 ELSE 0 END) AS move "
            "FROM md_inventory_record WHERE task_id=?", (int(task_id),)
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["expect"]:
        return {"expect": 0, "checked": 0, "normal": 0, "loss": 0,
                "profit": 0, "move": 0, "progress": 0.0, "diff_rate": 0.0}
    expect = int(row["expect"])
    checked = int(row["checked"] or 0)
    diff = int((row["loss"] or 0) + (row["profit"] or 0) + (row["move"] or 0))
    return {
        "expect": expect,
        "checked": checked,
        "normal": int(row["normal"] or 0),
        "loss": int(row["loss"] or 0),
        "profit": int(row["profit"] or 0),
        "move": int(row["move"] or 0),
        "progress": round(checked / expect * 100, 2),
        "diff_rate": round(diff / checked * 100, 2) if checked else 0.0,
    }


def build_inventory_charts(task_id):
    """组织盘点图表：账实结果分布与科室盘点完成率。"""
    conn = get_connection()
    try:
        result_rows = conn.execute(
            "SELECT check_result, COUNT(1) AS amount FROM md_inventory_record "
            "WHERE task_id=? GROUP BY check_result", (int(task_id),)
        ).fetchall()
        dept_rows = conn.execute(
            "SELECT dept_name, COUNT(1) AS expect, "
            "SUM(CASE WHEN check_result<>'PENDING' THEN 1 ELSE 0 END) AS checked "
            "FROM md_inventory_record WHERE task_id=? GROUP BY dept_name",
            (int(task_id),)
        ).fetchall()
    finally:
        conn.close()

    distribution = []
    for row in result_rows:
        distribution.append({
            "name": CHECK_RESULT_DICT.get(row["check_result"], row["check_result"]),
            "code": row["check_result"],
            "value": row["amount"],
        })
    dept_progress = []
    for row in dept_rows:
        expect = int(row["expect"])
        checked = int(row["checked"] or 0)
        dept_progress.append({
            "dept_name": row["dept_name"] or "未分配",
            "progress": round(checked / expect * 100, 2) if expect else 0.0,
            "checked": checked,
            "expect": expect,
        })
    dept_progress.sort(key=lambda x: x["progress"], reverse=True)
    return {"check_distribution": distribution, "dept_progress": dept_progress}


def build_inventory_payload(task_id=0, keyword="", check_result=""):
    """组装盘点管理页数据：任务列表、当前任务明细、汇总与图表。"""
    task_list = fetch_inventory_tasks()
    if not task_id and task_list:
        task_id = task_list[0]["task_id"]
    current_task = None
    for task in task_list:
        if task["task_id"] == int(task_id):
            current_task = task
            break
    return {
        "task_list": task_list,
        "current_task": current_task,
        "summary": summarize_inventory_task(task_id) if task_id else {},
        "records": fetch_inventory_records(task_id, keyword, check_result),
        "charts": build_inventory_charts(task_id) if task_id else {},
        "check_options": [{"code": k, "label": v} for k, v in CHECK_RESULT_DICT.items()
                          if k != "PENDING"],
        "status_options": [{"code": k, "label": v} for k, v in ASSET_STATUS_DICT.items()],
        "category_options": [{"code": k, "label": v} for k, v in ASSET_CATEGORY_DICT.items()],
        "dept_options": sorted({t["dept_scope"] for t in task_list if t["dept_scope"]}),
    }


def build_inventory_brief():
    """为运维总览看板提供盘点与维保概览小卡片数据。"""
    plan_rows = build_maintain_plan_rows()
    summary = summarize_maintain_plan(plan_rows)
    task_list = fetch_inventory_tasks(1)
    progress = task_list[0]["progress"] if task_list else 0.0
    return {
        "overdue_amount": summary["overdue_amount"],
        "urgent_amount": summary["urgent_amount"],
        "year_cost": summary["year_cost"],
        "inventory_progress": progress,
    }


def seed_demo_inventory():
    """灌入演示用盘点任务与维保记录，保证第 5 天启动即可看到页面效果。

    盘点任务按全科室生成，随机给出大部分「账实相符」与少量差异样本；
    维保记录覆盖四种维保类型，并使部分设备处于逾期状态。
    """
    conn = get_connection()
    try:
        exist = conn.execute(
            "SELECT COUNT(1) AS c FROM md_inventory_task").fetchone()["c"]
        if exist == 0:
            task_code = generate_task_code()
            cur = conn.execute(
                "INSERT INTO md_inventory_task (task_code, task_name, dept_scope, "
                "category_scope, plan_start, plan_end, task_status, expect_amount, "
                "checked_amount, diff_amount, operator_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (task_code, "2026 年度全院设备全面盘点", "", "",
                 today_text(), today_text(), "DOING", 0, 0, 0, "system"),
            )
            task_id = cur.lastrowid
            assets = conn.execute(
                "SELECT asset_id, asset_code, asset_name, dept_name, location_text, "
                "current_status FROM md_asset_ledger "
                "WHERE current_status NOT IN ('SCRAP') ORDER BY asset_id"
            ).fetchall()
            conn.executemany(
                "INSERT INTO md_inventory_record (task_id, asset_id, asset_code, "
                "asset_name, dept_name, book_status, book_location, check_result, "
                "location_match, remark_text) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(task_id, a["asset_id"], a["asset_code"], a["asset_name"], a["dept_name"],
                  a["current_status"], a["location_text"], "PENDING", 1, "")
                 for a in assets],
            )
            # 演示盘点进度：前 5 台标记为账实相符，第 6 台标记盘亏
            demo_results = ["NORMAL", "NORMAL", "MOVE", "NORMAL", "NORMAL", "LOSS"]
            for idx, result in enumerate(demo_results):
                conn.execute(
                    "UPDATE md_inventory_record SET actual_status=book_status, "
                    "check_result=?, location_match=?, checker_name='demo', "
                    "check_time=datetime('now','localtime') "
                    "WHERE record_id=(SELECT record_id FROM md_inventory_record "
                    "WHERE task_id=? ORDER BY record_id LIMIT 1 OFFSET ?)",
                    (result, 0 if result == "MOVE" else 1, task_id, idx),
                )
            counter = conn.execute(
                "SELECT SUM(CASE WHEN check_result<>'PENDING' THEN 1 ELSE 0 END) AS checked, "
                "SUM(CASE WHEN check_result<>'PENDING' AND check_result<>'NORMAL' THEN 1 ELSE 0 END) AS diff "
                "FROM md_inventory_record WHERE task_id=?", (task_id,)
            ).fetchone()
            conn.execute(
                "UPDATE md_inventory_task SET expect_amount=?, checked_amount=?, "
                "diff_amount=? WHERE task_id=?",
                (len(assets), int(counter["checked"]), int(counter["diff"] or 0), task_id),
            )

        demo_count = conn.execute(
            "SELECT COUNT(1) AS c FROM md_maintain_record").fetchone()["c"]
        amount = 0
        if demo_count == 0:
            assets = conn.execute(
                "SELECT asset_code, asset_name, dept_name, maintain_cycle "
                "FROM md_asset_ledger ORDER BY asset_id LIMIT 6"
            ).fetchall()
            type_cycle = ["ROUTINE", "PREVENT", "CALIB", "REPAIR", "ROUTINE", "PREVENT"]
            demo_rows = []
            for idx, asset in enumerate(assets):
                offset = -(30 * (idx + 1))
                work_day = datetime.date.today() + datetime.timedelta(days=offset)
                cycle = int(asset["maintain_cycle"] or 0) or DEFAULT_MAINTAIN_CYCLE
                # 前两台故意把下次维保日期放在过去，制造逾期样本
                next_day = work_day + datetime.timedelta(
                    days=cycle if idx >= 2 else int(cycle * 0.4))
                demo_rows.append((
                    asset["asset_code"], asset["asset_name"], asset["dept_name"],
                    type_cycle[idx % len(type_cycle)], work_day.strftime("%Y-%m-%d"),
                    "院内医学工程科", "工程师-%02d" % (idx + 1),
                    "按维保规程完成保养与功能检测", round(1200 + idx * 350, 2),
                    "PASS", next_day.strftime("%Y-%m-%d"),
                ))
            conn.executemany(
                "INSERT INTO md_maintain_record (asset_code, asset_name, dept_name, "
                "maintain_type, maintain_date, vendor_text, engineer_name, content_text, "
                "maintain_cost, result_code, next_maintain_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                demo_rows)
            conn.commit()
            amount = len(demo_rows)
    finally:
        conn.close()
    if amount:
        write_ops_journal("盘点维保", "初始化", "DEMO-INVENTORY",
                          "灌入演示维保记录 %d 条" % amount)
    return amount

# --------------------------------------------------------------------------
# 第 5 天数据表脚本增量：mdm_schema.sql（M，已内联为字符串常量 DAY5_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY5_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 5 天新增：设备盘点任务表
-- ==================================================================
-- 盘点任务表：一次盘点行动的主记录，记录盘点范围、计划时间与完成情况，
-- 盘点明细按任务编号挂载，任务完成后可归档留痕
CREATE TABLE IF NOT EXISTS md_inventory_task (
    task_id         INTEGER PRIMARY KEY AUTOINCREMENT,  -- 任务主键
    task_code       TEXT    NOT NULL UNIQUE,            -- 盘点任务编号（PD-日期-序号）
    task_name       TEXT    DEFAULT '',                 -- 盘点任务名称
    dept_scope      TEXT    DEFAULT '',                 -- 盘点范围科室（空表示全院）
    category_scope  TEXT    DEFAULT '',                 -- 盘点范围分类（空表示全部）
    plan_start      TEXT    DEFAULT '',                 -- 计划开始日期
    plan_end        TEXT    DEFAULT '',                 -- 计划结束日期
    task_status     TEXT    DEFAULT 'PLAN',             -- 任务状态（见 INVENTORY_TASK_STATUS）
    expect_amount   INTEGER DEFAULT 0,                  -- 应盘设备台数
    checked_amount  INTEGER DEFAULT 0,                  -- 已盘设备台数
    diff_amount     INTEGER DEFAULT 0,                  -- 账实差异台数
    operator_name   TEXT    DEFAULT '',                 -- 任务创建人
    created_at      TEXT    DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 5 天新增：设备盘点明细表
-- ==================================================================
-- 盘点明细表：每台设备在每个盘点任务下生成一条明细，记录账面状态与实盘
-- 状态的比对结果，是账实差异统计与盘点完成率计算的数据来源
CREATE TABLE IF NOT EXISTS md_inventory_record (
    record_id      INTEGER PRIMARY KEY AUTOINCREMENT,   -- 明细主键
    task_id        INTEGER NOT NULL,                    -- 所属盘点任务
    asset_id       INTEGER NOT NULL,                    -- 关联设备台账
    asset_code     TEXT    DEFAULT '',                  -- 资产编号快照
    asset_name     TEXT    DEFAULT '',                  -- 设备名称快照
    dept_name      TEXT    DEFAULT '',                  -- 使用科室快照
    book_status    TEXT    DEFAULT '',                  -- 账面状态
    book_location  TEXT    DEFAULT '',                  -- 账面存放位置
    actual_status  TEXT    DEFAULT '',                  -- 实盘状态
    check_result   TEXT    DEFAULT 'PENDING',           -- 核对结果（见 CHECK_RESULT_DICT）
    location_match INTEGER DEFAULT 1,                   -- 位置是否一致：1 一致 / 0 不一致
    remark_text    TEXT    DEFAULT '',                  -- 差异说明
    checker_name   TEXT    DEFAULT '',                  -- 盘点人
    check_time     TEXT    DEFAULT '',                  -- 盘点时间
    FOREIGN KEY (task_id) REFERENCES md_inventory_task (task_id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id) REFERENCES md_asset_ledger (asset_id) ON DELETE CASCADE
);

-- ==================================================================
-- 第 5 天新增：设备维保记录表
-- ==================================================================
-- 维保记录表：登记每次保养/维修/校准的过程与费用，结论合格时回滚台账的
-- 下次维保日期，支撑维保闭环管理与年度维保费用归集
CREATE TABLE IF NOT EXISTS md_maintain_record (
    record_id        INTEGER PRIMARY KEY AUTOINCREMENT, -- 维保记录主键
    asset_code       TEXT    NOT NULL,                  -- 关联设备资产编号
    asset_name       TEXT    DEFAULT '',                -- 设备名称快照
    dept_name        TEXT    DEFAULT '',                -- 使用科室快照
    maintain_type    TEXT    DEFAULT 'ROUTINE',         -- 维保类型（见 MAINTAIN_TYPE_DICT）
    maintain_date    TEXT    DEFAULT '',                -- 维保实施日期
    vendor_text      TEXT    DEFAULT '',                -- 维保单位
    engineer_name    TEXT    DEFAULT '',                -- 维保工程师
    content_text     TEXT    DEFAULT '',                -- 维保内容说明
    maintain_cost    REAL    DEFAULT 0,                 -- 维保费用（元）
    result_code      TEXT    DEFAULT 'PASS',            -- 维保结论（见 MAINTAIN_RESULT_DICT）
    next_maintain_at TEXT    DEFAULT '',                -- 本次维保后推算的下次维保日期
    created_at       TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_invtask_code   ON md_inventory_task (task_code);
CREATE INDEX IF NOT EXISTS idx_invrec_task    ON md_inventory_record (task_id);
CREATE INDEX IF NOT EXISTS idx_invrec_asset   ON md_inventory_record (asset_code);
CREATE INDEX IF NOT EXISTS idx_invrec_result  ON md_inventory_record (check_result);
CREATE INDEX IF NOT EXISTS idx_maintain_code  ON md_maintain_record (asset_code);
CREATE INDEX IF NOT EXISTS idx_maintain_date  ON md_maintain_record (maintain_date);
"""


def apply_day5_schema(conn):
    """把第 5 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY5_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 5 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-25（第 5 天 / 共 15 天）
#           注册运维总览看板、设备台账 CRUD、设备状态管理、ROI 投入产出
#           分析与盘点维保管理五套页面路由，并提供台账增删改查、闲置识别
#           扫描、状态切换、ROI 测算与年度收支档案维护、盘点任务生成与
#           账实核对、维保登记等 RESTful 接口，供前端表格、弹窗与 ECharts
#           图表异步取数

from mdm_inventory import (
    build_inventory_payload,
    build_inventory_brief,
    create_inventory_task,
    save_inventory_check,
    build_maintain_payload,
    save_maintain_record,
    fetch_maintain_records,
    seed_demo_inventory,
    CHECK_RESULT_DICT,
    MAINTAIN_TYPE_DICT,
    MAINTAIN_RESULT_DICT,
    DUE_LEVEL_DICT,
)

# 【函数内插入】def api_overview():
#    payload["inventory_brief"] = build_inventory_brief()

# ==================================================================
# 以下为第 5 天新增：设备盘点与维保记录管理接口
# ==================================================================


@app.route("/md/inventory")
def panel_inventory():
    """盘点与维保管理页面：维保到期概览、盘点进度与账实核对明细。"""
    write_ops_journal("盘点维保", "查询", "PANEL-INVENTORY", "打开盘点与维保管理页面")
    return render_template(
        "panel_inventory.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/md/maintain/panel")
def api_maintain_panel():
    """维保管理数据接口：到期清单、汇总、记录与三类分析图表。"""
    payload = build_maintain_payload(
        asset_code=(request.args.get("asset_code") or "").strip(),
        limit=int(request.args.get("limit", 50)),
    )
    payload["result"] = "OK"
    return jsonify(payload)


@app.route("/api/md/maintain/record", methods=["GET", "POST"])
def api_maintain_record():
    """维保记录接口：GET 按设备查询历史，POST 登记一条新的维保记录。"""
    if request.method == "GET":
        return jsonify({
            "result": "OK",
            "records": fetch_maintain_records(
                (request.args.get("asset_code") or "").strip(),
                int(request.args.get("limit", 50)),
            ),
        })
    payload = request.get_json(silent=True) or request.form.to_dict()
    outcome = save_maintain_record(
        (payload.get("asset_code") or "").strip(),
        (payload.get("maintain_type") or "").strip(),
        (payload.get("maintain_date") or "").strip(),
        payload.get("vendor_text") or "",
        payload.get("engineer_name") or "",
        payload.get("content_text") or "",
        payload.get("maintain_cost") or 0,
        (payload.get("result_code") or "").strip(),
    )
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/md/inventory/panel")
def api_inventory_panel():
    """盘点管理数据接口：任务列表、当前任务明细、汇总与盘点图表。"""
    payload = build_inventory_payload(
        task_id=int(request.args.get("task_id") or 0),
        keyword=(request.args.get("keyword") or "").strip(),
        check_result=(request.args.get("check_result") or "").strip(),
    )
    payload["result"] = "OK"
    return jsonify(payload)


@app.route("/api/md/inventory/task", methods=["GET", "POST"])
def api_inventory_task():
    """盘点任务接口：GET 取任务清单，POST 按范围创建任务并生成明细。"""
    if request.method == "GET":
        return jsonify({"result": "OK", "tasks": build_inventory_payload()["task_list"]})
    payload = request.get_json(silent=True) or request.form.to_dict()
    outcome = create_inventory_task(
        (payload.get("task_name") or "").strip(),
        (payload.get("dept_name") or "").strip(),
        (payload.get("category_code") or "").strip(),
        (payload.get("plan_start") or "").strip(),
        (payload.get("plan_end") or "").strip(),
        operator="admin",
    )
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/md/inventory/check", methods=["POST"])
def api_inventory_check():
    """账实核对录入接口：登记单条盘点结果并回写任务进度。"""
    payload = request.get_json(silent=True) or request.form.to_dict()
    outcome = save_inventory_check(
        int(payload.get("record_id") or 0),
        (payload.get("check_result") or "").strip(),
        (payload.get("actual_status") or "").strip(),
        int(payload.get("location_match") or 1),
        payload.get("remark_text") or "",
        checker_name="admin",
    )
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/md/inventory/options")
def api_inventory_options():
    """盘点维保页下拉选项接口：维保类型、维保结论、核对结果与设备状态。"""
    return jsonify({
        "result": "OK",
        "type_options": [{"code": k, "label": v} for k, v in MAINTAIN_TYPE_DICT.items()],
        "result_options": [{"code": k, "label": v} for k, v in MAINTAIN_RESULT_DICT.items()],
        "check_options": [{"code": k, "label": v} for k, v in CHECK_RESULT_DICT.items()
                          if k != "PENDING"],
        "due_options": [{"code": k, "label": v} for k, v in DUE_LEVEL_DICT.items()],
        "status_options": [{"code": k, "label": v} for k, v in ASSET_STATUS_DICT.items()],
    })



# 【函数内插入】def bootstrap_application():
#    seed_demo_inventory()

# 【函数内插入】if __name__ == "__main__":
#    print("盘点维保： http://127.0.0.1:%d/md/inventory" % PORT)

# --------------------------------------------------------------------------
# 第 5 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/mdm_inventory.js          新增 466 行
#   [修改] templates/layout_mdm.html        新增 1 行
#   [新增] templates/panel_inventory.html   新增 318 行
