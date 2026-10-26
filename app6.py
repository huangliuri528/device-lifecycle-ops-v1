# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-26（第 6 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 6 天新增与修改的源代码，不含第 1~5 天已有代码
#           本次迭代主题：运维日志查询与导出模块 + 运维日志页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5001/md/overview）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 6 天新增业务模块 1：mdm_log.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-26（第 6 天 / 共 15 天）
# 模块功能：运维日志查询模块。负责设备运维作业日志的登记归档与多条件
#           组合检索，支持按日志来源、事件类型、使用科室、处理结果、
#           发生日期区间与关键字筛选并分页，输出日志来源分布、事件类型
#           分布、近 6 个月日志量与费用趋势、科室日志分布、处理人排行
#           等统计图表，提供运维日志 CSV 导出与导出批次留痕，同时接入
#           系统操作流水（md_ops_journal）的查询，形成「设备作业日志 +
#           系统操作流水」双层日志追溯能力
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）、csv（Python 内置）

import datetime
import io
import csv

from mdm_settings import ASSET_CATEGORY_DICT
from mdm_dal import get_connection, write_ops_journal

# 日志来源字典：区分日志由哪个业务环节产生，便于按环节归集工作量
LOG_SOURCE_DICT = {
    "MAINTAIN": "维保保养",
    "INSPECT": "日常巡检",
    "REPAIR": "故障维修",
    "CALIB": "计量校准",
    "MOVE": "调拨移机",
    "OTHER": "其他事项",
}

# 事件类型字典：同一来源下可细分的具体作业类型
LOG_TYPE_DICT = {
    "ROUTINE": "例行作业",
    "TROUBLE": "故障处理",
    "URGENT": "应急抢修",
    "PLAN": "计划性作业",
    "IMPROVE": "整改改进",
}

# 处理结果字典：未完成（PENDING）的日志需要在后续跟踪闭环
LOG_RESULT_DICT = {
    "DONE": "已完成",
    "DOING": "处理中",
    "PENDING": "待跟进",
}

# 导出文件格式字典：导出时按格式生成文本并留痕批次
EXPORT_FORMAT_DICT = {
    "CSV": "CSV 文本",
}

# 单页默认条数与单页最大条数，防止一次性查询过大拖慢页面
LOG_PAGE_SIZE = 10
LOG_PAGE_MAX = 100

# 日志编号前缀，编号形如 OPS-2026-0001
LOG_CODE_PREFIX = "OPS"


def today_text():
    """返回当前日期文本，格式 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def current_year():
    """返回当前年份文本，用于生成年度日志编号。"""
    return datetime.date.today().strftime("%Y")


def month_tag(date_text):
    """取日期文本所在月份标签，格式 YYYY-MM（非法日期返回空串）。"""
    if not date_text:
        return ""
    return str(date_text)[:7]


def shift_days_text(days):
    """返回相对今天偏移指定天数后的日期文本，负数表示往前推。"""
    return (datetime.date.today() + datetime.timedelta(days=days)).strftime("%Y-%m-%d")


def recent_month_tags(amount=6):
    """生成最近 N 个月的月份标签列表，用于月度趋势图的横轴。"""
    tags = []
    today = datetime.date.today()
    for offset in range(amount - 1, -1, -1):
        month_index = today.month - offset
        year_value = today.year + (month_index - 1) // 12
        month_value = (month_index - 1) % 12 + 1
        tags.append("%04d-%02d" % (year_value, month_value))
    return tags


def build_log_code(seq_no):
    """按「前缀-年份-四位序号」生成日志编号。"""
    return "%s-%s-%04d" % (LOG_CODE_PREFIX, current_year(), int(seq_no or 0))


def fetch_asset_brief(asset_code):
    """按资产编号读取设备名称、科室与分类，用于登记日志时做快照。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT asset_code, asset_name, dept_name, asset_category "
            "FROM md_asset_ledger WHERE asset_code=?", (asset_code,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def build_filter_clause(keyword="", source_code="", type_code="",
                        dept_name="", result_code="", date_from="", date_to=""):
    """把多条件筛选拼成 SQL 的 WHERE 子句与参数列表。

    关键字同时匹配日志编号、资产编号、设备名称、处理人与内容摘要；
    日期区间按发生日期过滤，区间只给一端时按单边处理。
    """
    clauses = []
    params = []
    if keyword:
        clauses.append(
            "(log_code LIKE ? OR asset_code LIKE ? OR asset_name LIKE ? "
            "OR handler_name LIKE ? OR content_text LIKE ?)")
        like_text = "%%%s%%" % keyword
        params.extend([like_text] * 5)
    if source_code:
        clauses.append("log_source=?")
        params.append(source_code)
    if type_code:
        clauses.append("log_type=?")
        params.append(type_code)
    if dept_name:
        clauses.append("dept_name=?")
        params.append(dept_name)
    if result_code:
        clauses.append("result_flag=?")
        params.append(result_code)
    if date_from:
        clauses.append("happen_date>=?")
        params.append(date_from)
    if date_to:
        clauses.append("happen_date<=?")
        params.append(date_to)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


def query_logbook(keyword="", source_code="", type_code="", dept_name="",
                  result_code="", date_from="", date_to="",
                  page=1, size=LOG_PAGE_SIZE):
    """分页查询运维作业日志，返回当前页明细与分页信息。"""
    where_sql, params = build_filter_clause(
        keyword, source_code, type_code, dept_name, result_code, date_from, date_to)
    size = max(1, min(int(size or LOG_PAGE_SIZE), LOG_PAGE_MAX))
    page = max(1, int(page or 1))

    conn = get_connection()
    try:
        total_row = conn.execute(
            "SELECT COUNT(1) AS total FROM md_ops_logbook" + where_sql, params
        ).fetchone()
        total = int(total_row["total"]) if total_row else 0
        page_amount = max(1, (total + size - 1) // size)
        if page > page_amount:
            page = page_amount
        rows = conn.execute(
            "SELECT * FROM md_ops_logbook" + where_sql +
            " ORDER BY happen_date DESC, log_id DESC LIMIT ? OFFSET ?",
            params + [size, (page - 1) * size]
        ).fetchall()
    finally:
        conn.close()

    records = []
    for item in [dict(r) for r in rows]:
        item["source_label"] = LOG_SOURCE_DICT.get(item["log_source"], "未分类")
        item["type_label"] = LOG_TYPE_DICT.get(item["log_type"], "未分类")
        item["result_label"] = LOG_RESULT_DICT.get(item["result_flag"], "未知")
        item["category_label"] = ASSET_CATEGORY_DICT.get(
            item.get("asset_category", ""), "未分类")
        records.append(item)
    return {
        "records": records,
        "total": total,
        "page": page,
        "size": size,
        "page_amount": page_amount,
    }


def build_log_statistics(keyword="", source_code="", type_code="", dept_name="",
                         result_code="", date_from="", date_to=""):
    """按当前筛选条件统计日志量与费用，并输出五类图表数据。"""
    where_sql, params = build_filter_clause(
        keyword, source_code, type_code, dept_name, result_code, date_from, date_to)

    conn = get_connection()
    try:
        summary_row = conn.execute(
            "SELECT COUNT(1) AS log_amount, "
            "COALESCE(SUM(cost_amount), 0) AS cost_total, "
            "COALESCE(SUM(duration_hours), 0) AS hour_total, "
            "SUM(CASE WHEN result_flag='DONE' THEN 1 ELSE 0 END) AS done_amount, "
            "SUM(CASE WHEN result_flag='PENDING' THEN 1 ELSE 0 END) AS pending_amount "
            "FROM md_ops_logbook" + where_sql, params
        ).fetchone()
        source_rows = conn.execute(
            "SELECT log_source, COUNT(1) AS amount, "
            "COALESCE(SUM(cost_amount), 0) AS cost_total "
            "FROM md_ops_logbook" + where_sql +
            " GROUP BY log_source ORDER BY amount DESC", params
        ).fetchall()
        type_rows = conn.execute(
            "SELECT log_type, COUNT(1) AS amount FROM md_ops_logbook" + where_sql +
            " GROUP BY log_type ORDER BY amount DESC", params
        ).fetchall()
        dept_rows = conn.execute(
            "SELECT dept_name, COUNT(1) AS amount, "
            "COALESCE(SUM(cost_amount), 0) AS cost_total "
            "FROM md_ops_logbook" + where_sql +
            " GROUP BY dept_name ORDER BY amount DESC LIMIT 8", params
        ).fetchall()
        handler_rows = conn.execute(
            "SELECT handler_name, COUNT(1) AS amount FROM md_ops_logbook" + where_sql +
            " GROUP BY handler_name ORDER BY amount DESC LIMIT 8", params
        ).fetchall()
        result_rows = conn.execute(
            "SELECT result_flag, COUNT(1) AS amount FROM md_ops_logbook" + where_sql +
            " GROUP BY result_flag", params
        ).fetchall()
        month_rows = conn.execute(
            "SELECT substr(happen_date, 1, 7) AS month_tag, COUNT(1) AS amount, "
            "COALESCE(SUM(cost_amount), 0) AS cost_total "
            "FROM md_ops_logbook" + where_sql +
            " GROUP BY month_tag", params
        ).fetchall()
    finally:
        conn.close()

    month_map = {row["month_tag"]: row for row in [dict(r) for r in month_rows]}
    month_tags = recent_month_tags(6)
    trend_amount = []
    trend_cost = []
    for tag in month_tags:
        hit = month_map.get(tag)
        trend_amount.append(int(hit["amount"]) if hit else 0)
        trend_cost.append(round(float(hit["cost_total"] or 0), 2) if hit else 0.0)

    summary = dict(summary_row) if summary_row else {}
    summary["cost_total"] = round(float(summary.get("cost_total") or 0), 2)
    summary["hour_total"] = round(float(summary.get("hour_total") or 0), 1)
    summary["done_amount"] = int(summary.get("done_amount") or 0)
    summary["pending_amount"] = int(summary.get("pending_amount") or 0)
    summary["log_amount"] = int(summary.get("log_amount") or 0)

    return {
        "summary": summary,
        "source_distribution": [
            {"name": LOG_SOURCE_DICT.get(r["log_source"], "未分类"),
             "value": int(r["amount"]),
             "cost": round(float(r["cost_total"] or 0), 2)}
            for r in [dict(x) for x in source_rows]
        ],
        "type_distribution": [
            {"name": LOG_TYPE_DICT.get(r["log_type"], "未分类"),
             "value": int(r["amount"])}
            for r in [dict(x) for x in type_rows]
        ],
        "dept_distribution": [
            {"name": r["dept_name"] or "未归属",
             "value": int(r["amount"]),
             "cost": round(float(r["cost_total"] or 0), 2)}
            for r in [dict(x) for x in dept_rows]
        ],
        "handler_ranking": [
            {"name": r["handler_name"] or "未指派", "value": int(r["amount"])}
            for r in [dict(x) for x in handler_rows]
        ],
        "result_distribution": [
            {"name": LOG_RESULT_DICT.get(r["result_flag"], "未知"),
             "value": int(r["amount"])}
            for r in [dict(x) for x in result_rows]
        ],
        "month_trend": {
            "categories": month_tags,
            "log_amount": trend_amount,
            "cost_amount": trend_cost,
        },
    }


def build_log_payload(keyword="", source_code="", type_code="", dept_name="",
                      result_code="", date_from="", date_to="",
                      page=1, size=LOG_PAGE_SIZE):
    """组装运维日志页面所需的全部数据：统计、图表、分页明细与下拉选项。"""
    statistics = build_log_statistics(
        keyword, source_code, type_code, dept_name, result_code, date_from, date_to)
    page_data = query_logbook(
        keyword, source_code, type_code, dept_name, result_code,
        date_from, date_to, page, size)

    conn = get_connection()
    try:
        dept_rows = conn.execute(
            "SELECT DISTINCT dept_name FROM md_ops_logbook "
            "WHERE dept_name<>'' ORDER BY dept_name"
        ).fetchall()
        asset_rows = conn.execute(
            "SELECT asset_code, asset_name FROM md_asset_ledger "
            "WHERE current_status<>'SCRAP' ORDER BY asset_code"
        ).fetchall()
    finally:
        conn.close()

    payload = {
        "summary": statistics["summary"],
        "charts": {
            "source_distribution": statistics["source_distribution"],
            "type_distribution": statistics["type_distribution"],
            "dept_distribution": statistics["dept_distribution"],
            "handler_ranking": statistics["handler_ranking"],
            "result_distribution": statistics["result_distribution"],
            "month_trend": statistics["month_trend"],
        },
        "records": page_data["records"],
        "total": page_data["total"],
        "page": page_data["page"],
        "size": page_data["size"],
        "page_amount": page_data["page_amount"],
        "dept_options": [r["dept_name"] for r in dept_rows],
        "asset_options": [dict(r) for r in asset_rows],
        "source_options": [{"code": k, "label": v} for k, v in LOG_SOURCE_DICT.items()],
        "type_options": [{"code": k, "label": v} for k, v in LOG_TYPE_DICT.items()],
        "result_options": [{"code": k, "label": v} for k, v in LOG_RESULT_DICT.items()],
    }
    return payload


def build_log_brief():
    """生成看板用的日志概览：本月日志量、本月费用与待跟进条数。"""
    month_prefix = month_tag(today_text())
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(1) AS total, "
            "SUM(CASE WHEN substr(happen_date,1,7)=? THEN 1 ELSE 0 END) AS month_amount, "
            "COALESCE(SUM(CASE WHEN substr(happen_date,1,7)=? "
            "THEN cost_amount ELSE 0 END), 0) AS month_cost, "
            "SUM(CASE WHEN result_flag<>'DONE' THEN 1 ELSE 0 END) AS open_amount "
            "FROM md_ops_logbook", (month_prefix, month_prefix)
        ).fetchone()
    finally:
        conn.close()
    item = dict(row) if row else {}
    return {
        "log_total": int(item.get("total") or 0),
        "month_amount": int(item.get("month_amount") or 0),
        "month_cost": round(float(item.get("month_cost") or 0), 2),
        "open_amount": int(item.get("open_amount") or 0),
    }


def save_log_record(asset_code, log_source, log_type, happen_date,
                    handler_name, content_text, cost_amount=0,
                    duration_hours=0, result_flag="DONE", operator="admin"):
    """登记一条设备运维作业日志，落库存档并写入系统操作流水。

    校验口径：资产编号必须存在于台账；来源、类型、结论必须是已登记编码；
    发生日期不能晚于今天；费用与耗时不能为负；内容摘要不能为空。
    """
    asset = fetch_asset_brief((asset_code or "").strip())
    if not asset:
        return {"success": False, "message": "资产编号不存在：%s" % asset_code}
    if log_source not in LOG_SOURCE_DICT:
        return {"success": False, "message": "日志来源不在字典范围内"}
    if log_type not in LOG_TYPE_DICT:
        return {"success": False, "message": "事件类型不在字典范围内"}
    if result_flag not in LOG_RESULT_DICT:
        return {"success": False, "message": "处理结果不在字典范围内"}
    try:
        happen = datetime.datetime.strptime(str(happen_date)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {"success": False, "message": "发生日期格式应为 YYYY-MM-DD"}
    if happen > datetime.date.today():
        return {"success": False, "message": "发生日期不能晚于今天"}
    if float(cost_amount or 0) < 0:
        return {"success": False, "message": "作业费用不能为负"}
    if float(duration_hours or 0) < 0:
        return {"success": False, "message": "作业耗时不能为负"}
    if not (content_text or "").strip():
        return {"success": False, "message": "内容摘要不能为空"}

    conn = get_connection()
    try:
        seq_row = conn.execute("SELECT COUNT(1) AS amount FROM md_ops_logbook").fetchone()
        log_code = build_log_code(int(seq_row["amount"]) + 1)
        conn.execute(
            "INSERT INTO md_ops_logbook "
            "(log_code, asset_code, asset_name, dept_name, asset_category, "
            "log_source, log_type, happen_date, handler_name, content_text, "
            "cost_amount, duration_hours, result_flag) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (log_code, asset["asset_code"], asset["asset_name"],
             asset["dept_name"], asset["asset_category"], log_source, log_type,
             happen.strftime("%Y-%m-%d"), (handler_name or "").strip(),
             (content_text or "").strip(), round(float(cost_amount or 0), 2),
             round(float(duration_hours or 0), 1), result_flag),
        )
        conn.commit()
    finally:
        conn.close()

    write_ops_journal("运维日志", "新增", log_code,
                      "登记 %s 的 %s 日志" % (asset["asset_code"],
                                          LOG_SOURCE_DICT.get(log_source)),
                      operator=operator)
    return {"success": True, "log_code": log_code, "message": "运维日志已登记：%s" % log_code}


def close_log_record(log_id, result_flag, operator="admin"):
    """闭环单条日志：把处理中/待跟进的日志置为已完成。"""
    if result_flag not in LOG_RESULT_DICT:
        return {"success": False, "message": "处理结果不在字典范围内"}
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT log_id, log_code, asset_code FROM md_ops_logbook WHERE log_id=?",
            (int(log_id or 0),)).fetchone()
        if not row:
            return {"success": False, "message": "日志不存在"}
        conn.execute(
            "UPDATE md_ops_logbook SET result_flag=?, closed_at=?"
            " WHERE log_id=?",
            (result_flag, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             int(log_id)),
        )
        conn.commit()
        log_code = row["log_code"]
        asset_code = row["asset_code"]
    finally:
        conn.close()
    write_ops_journal("运维日志", "修改", log_code,
                      "更新处理结果为 %s" % LOG_RESULT_DICT.get(result_flag),
                      operator=operator)
    return {"success": True, "message": "%s 处理结果已更新为%s"
            % (asset_code, LOG_RESULT_DICT.get(result_flag))}


def export_logbook_csv(keyword="", source_code="", type_code="", dept_name="",
                       result_code="", date_from="", date_to="", operator="admin"):
    """按当前筛选条件导出运维日志为 CSV 文本，并登记导出批次留痕。"""
    where_sql, params = build_filter_clause(
        keyword, source_code, type_code, dept_name, result_code, date_from, date_to)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM md_ops_logbook" + where_sql +
            " ORDER BY happen_date DESC, log_id DESC", params
        ).fetchall()
    finally:
        conn.close()
    records = [dict(r) for r in rows]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["日志编号", "资产编号", "设备名称", "使用科室", "日志来源",
                     "事件类型", "发生日期", "处理人", "作业费用(元)",
                     "作业耗时(小时)", "处理结果", "内容摘要"])
    for item in records:
        writer.writerow([
            item["log_code"], item["asset_code"], item["asset_name"],
            item["dept_name"], LOG_SOURCE_DICT.get(item["log_source"], ""),
            LOG_TYPE_DICT.get(item["log_type"], ""), item["happen_date"],
            item["handler_name"], item["cost_amount"], item["duration_hours"],
            LOG_RESULT_DICT.get(item["result_flag"], ""), item["content_text"],
        ])
    csv_text = buffer.getvalue()
    buffer.close()

    batch_code = "EXP-%s" % datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    condition_text = "关键字=%s;来源=%s;类型=%s;科室=%s;结论=%s;区间=%s~%s" % (
        keyword or "-", source_code or "-", type_code or "-", dept_name or "-",
        result_code or "-", date_from or "-", date_to or "-")
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO md_log_export "
            "(batch_code, condition_text, record_amount, file_format, operator_name) "
            "VALUES (?,?,?,?,?)",
            (batch_code, condition_text, len(records), "CSV", operator),
        )
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("运维日志", "导出", batch_code,
                      "导出运维日志 %d 条" % len(records), operator=operator)
    return {
        "success": True,
        "batch_code": batch_code,
        "file_name": "运维日志_%s.csv" % datetime.datetime.now().strftime("%Y%m%d"),
        "record_amount": len(records),
        "csv_text": csv_text,
    }


def fetch_export_batches(limit=10):
    """读取最近的日志导出批次记录，用于导出留痕展示。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM md_log_export ORDER BY export_id DESC LIMIT ?",
            (int(limit or 10),)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def query_journal_page(keyword="", module_name="", action_type="",
                       date_from="", date_to="", page=1, size=8):
    """分页查询系统操作流水（md_ops_journal），支撑操作留痕追溯。"""
    clauses = []
    params = []
    if keyword:
        clauses.append("(target_code LIKE ? OR action_detail LIKE ? "
                       "OR operator_name LIKE ?)")
        like_text = "%%%s%%" % keyword
        params.extend([like_text] * 3)
    if module_name:
        clauses.append("module_name=?")
        params.append(module_name)
    if action_type:
        clauses.append("action_type=?")
        params.append(action_type)
    if date_from:
        clauses.append("substr(action_time,1,10)>=?")
        params.append(date_from)
    if date_to:
        clauses.append("substr(action_time,1,10)<=?")
        params.append(date_to)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = get_connection()
    try:
        total_row = conn.execute(
            "SELECT COUNT(1) AS total FROM md_ops_journal" + where_sql, params
        ).fetchone()
        total = int(total_row["total"]) if total_row else 0
        rows = conn.execute(
            "SELECT * FROM md_ops_journal" + where_sql +
            " ORDER BY journal_id DESC LIMIT ? OFFSET ?",
            params + [int(size), max(0, (int(page) - 1) * int(size))]
        ).fetchall()
        module_rows = conn.execute(
            "SELECT DISTINCT module_name FROM md_ops_journal "
            "WHERE module_name<>'' ORDER BY module_name").fetchall()
    finally:
        conn.close()
    return {
        "records": [dict(r) for r in rows],
        "total": total,
        "module_options": [r["module_name"] for r in module_rows],
    }


def seed_demo_logbook():
    """灌入演示用运维日志：覆盖各来源、类型、科室与处理结果，便于首日可查。

    已有日志时跳过，避免重复灌数。
    """
    conn = get_connection()
    try:
        exist = conn.execute(
            "SELECT COUNT(1) AS amount FROM md_ops_logbook").fetchone()["amount"]
        if exist > 0:
            return 0
        asset_rows = conn.execute(
            "SELECT asset_code, asset_name, dept_name, asset_category "
            "FROM md_asset_ledger ORDER BY asset_id").fetchall()
    finally:
        conn.close()
    assets = [dict(r) for r in asset_rows]
    if not assets:
        return 0

    demo_plan = [
        (0, "MAINTAIN", "ROUTINE", -3, "赵强", "按季度保养：清洁光路并校验样本针", 800, 2.0, "DONE"),
        (1, "REPAIR", "TROUBLE", -6, "孙明", "球管报错停机，更换高压模块后恢复", 12600, 6.5, "DONE"),
        (2, "INSPECT", "ROUTINE", -2, "周敏", "日常巡检：监护仪导联线与报警音检测", 0, 1.0, "DONE"),
        (3, "CALIB", "PLAN", -11, "吴涛", "年度计量校准，出具校准证书", 1500, 3.0, "DONE"),
        (4, "MAINTAIN", "PLAN", -18, "赵强", "预防性维护：更换滤网与散热风扇", 640, 2.5, "DONE"),
        (5, "REPAIR", "URGENT", -1, "孙明", "输液泵流速异常，应急抢修更换蠕动组件", 3200, 4.0, "DOING"),
        (6, "MOVE", "IMPROVE", -9, "李娜", "由门诊楼调拨至急诊楼，完成位置变更登记", 0, 1.5, "DONE"),
        (7, "INSPECT", "ROUTINE", -5, "周敏", "巡检发现外壳接地不良，已现场整改", 120, 1.0, "DONE"),
        (0, "REPAIR", "TROUBLE", -25, "孙明", "试剂仓温控偏差，更换温控探头", 2400, 3.5, "DONE"),
        (1, "INSPECT", "PLAN", -14, "赵强", "季度巡检：曝光参数与图像噪声复核", 0, 2.0, "DONE"),
        (2, "MAINTAIN", "ROUTINE", -33, "周敏", "保养：屏幕清洁与电池容量检测", 300, 1.5, "DONE"),
        (3, "REPAIR", "TROUBLE", -40, "孙明", "主板故障，返厂维修后回装", 8600, 12.0, "DONE"),
        (4, "CALIB", "PLAN", -52, "吴涛", "计量校准：压力与流量示值误差复核", 1200, 2.5, "DONE"),
        (5, "INSPECT", "IMPROVE", -21, "赵强", "巡检发现报警记录未归档，已补录并整改", 0, 1.0, "DONE"),
        (6, "MAINTAIN", "PLAN", -47, "李娜", "半年度保养：机械臂润滑与精度复核", 1800, 4.0, "DONE"),
        (7, "REPAIR", "URGENT", -30, "孙明", "呼吸机氧浓度偏差，应急校准处理", 4200, 5.0, "DONE"),
        (0, "OTHER", "IMPROVE", -7, "张伟", "配合院方质控检查，整理设备档案", 0, 2.0, "PENDING"),
        (1, "INSPECT", "ROUTINE", -60, "周敏", "上半年巡检：机房温湿度与电源检查", 0, 1.5, "DONE"),
        (2, "REPAIR", "TROUBLE", -16, "孙明", "心电模块干扰，更换屏蔽线后恢复", 900, 2.0, "DOING"),
        (3, "MAINTAIN", "ROUTINE", -4, "赵强", "保养：镜片清洁与光路校正", 500, 2.0, "DONE"),
        (4, "MOVE", "PLAN", -70, "李娜", "科室合并，设备随科室整体搬迁", 600, 3.0, "DONE"),
        (5, "CALIB", "PLAN", -12, "吴涛", "计量校准：流速与压力双参数复核", 1400, 2.5, "PENDING"),
        (6, "INSPECT", "ROUTINE", -8, "周敏", "巡检：报警限值设置与记录完整性检查", 0, 1.0, "DONE"),
        (7, "MAINTAIN", "ROUTINE", -37, "赵强", "保养：气路密封性检查与过滤器更换", 760, 2.0, "DONE"),
    ]

    conn = get_connection()
    try:
        for index, plan in enumerate(demo_plan):
            asset_index = plan[0] % len(assets)
            asset = assets[asset_index]
            conn.execute(
                "INSERT INTO md_ops_logbook "
                "(log_code, asset_code, asset_name, dept_name, asset_category, "
                "log_source, log_type, happen_date, handler_name, content_text, "
                "cost_amount, duration_hours, result_flag) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (build_log_code(index + 1), asset["asset_code"], asset["asset_name"],
                 asset["dept_name"], asset["asset_category"], plan[1], plan[2],
                 shift_days_text(plan[3]), plan[4], plan[5], plan[6], plan[7], plan[8]),
            )
        conn.commit()
    finally:
        conn.close()
    return len(demo_plan)

# --------------------------------------------------------------------------
# 第 6 天数据表脚本增量：mdm_schema.sql（M，已内联为字符串常量 DAY6_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY6_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 6 天新增：设备运维作业日志表
-- ==================================================================
-- 运维作业日志表：按「一台设备一次作业一条日志」记录运维全过程，含来源
-- 环节、事件类型、处理人、费用与耗时、处理结果，支撑多条件组合检索、
-- 工作量归集与运维成本分析
CREATE TABLE IF NOT EXISTS md_ops_logbook (
    log_id         INTEGER PRIMARY KEY AUTOINCREMENT,  -- 日志主键
    log_code       TEXT    DEFAULT '',                 -- 日志编号（OPS-年份-序号）
    asset_code     TEXT    NOT NULL,                   -- 资产编号
    asset_name     TEXT    DEFAULT '',                 -- 设备名称快照
    dept_name      TEXT    DEFAULT '',                 -- 使用科室快照
    asset_category TEXT    DEFAULT '',                 -- 设备分类快照
    log_source     TEXT    DEFAULT 'MAINTAIN',         -- 日志来源（见 LOG_SOURCE_DICT）
    log_type       TEXT    DEFAULT 'ROUTINE',          -- 事件类型（见 LOG_TYPE_DICT）
    happen_date    TEXT    DEFAULT '',                 -- 作业发生日期
    handler_name   TEXT    DEFAULT '',                 -- 处理人
    content_text   TEXT    DEFAULT '',                 -- 作业内容摘要
    cost_amount    REAL    DEFAULT 0,                  -- 作业费用（元）
    duration_hours REAL    DEFAULT 0,                  -- 作业耗时（小时）
    result_flag    TEXT    DEFAULT 'DONE',             -- 处理结果（见 LOG_RESULT_DICT）
    closed_at      TEXT    DEFAULT '',                 -- 闭环时间
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 6 天新增：运维日志导出批次表
-- ==================================================================
-- 导出批次表：记录每一次日志导出的筛选条件、条数、格式与操作人，
-- 使导出行可追溯，满足审计留痕要求
CREATE TABLE IF NOT EXISTS md_log_export (
    export_id      INTEGER PRIMARY KEY AUTOINCREMENT,  -- 批次主键
    batch_code     TEXT    DEFAULT '',                 -- 导出批次号
    condition_text TEXT    DEFAULT '',                 -- 导出时的筛选条件摘要
    record_amount  INTEGER DEFAULT 0,                  -- 导出条数
    file_format    TEXT    DEFAULT 'CSV',              -- 导出文件格式
    operator_name  TEXT    DEFAULT 'admin',            -- 导出操作人
    export_time    TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_logbook_code    ON md_ops_logbook (log_code);
CREATE INDEX IF NOT EXISTS idx_logbook_asset   ON md_ops_logbook (asset_code);
CREATE INDEX IF NOT EXISTS idx_logbook_dept    ON md_ops_logbook (dept_name);
CREATE INDEX IF NOT EXISTS idx_logbook_source  ON md_ops_logbook (log_source);
CREATE INDEX IF NOT EXISTS idx_logbook_date    ON md_ops_logbook (happen_date);
CREATE INDEX IF NOT EXISTS idx_logexport_time  ON md_log_export (export_time);
"""


def apply_day6_schema(conn):
    """把第 6 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY6_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 6 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-26（第 6 天 / 共 15 天）
#           分析、盘点维保管理与运维日志查询六套页面路由，并提供台账增删
#           改查、闲置识别扫描、状态切换、ROI 测算与年度收支档案维护、盘点
#           任务生成与账实核对、维保登记，以及运维日志检索、登记、闭环与
#           CSV 导出等 RESTful 接口，供前端表格、弹窗与 ECharts 图表异步取数
from mdm_settings import ASSET_STATUS_DICT

from mdm_log import (
    build_log_payload,
    build_log_brief,
    save_log_record,
    close_log_record,
    export_logbook_csv,
    fetch_export_batches,
    query_journal_page,
    seed_demo_logbook,
)

# 【函数内插入】def api_overview():
#    payload["log_brief"] = build_log_brief()

# ==================================================================
# 以下为第 6 天新增：运维日志查询与导出接口
# ==================================================================


@app.route("/md/logs")
def panel_logs():
    """运维日志查询页面：日志概览、多维分析图表、明细检索与导出留痕。"""
    write_ops_journal("运维日志", "查询", "PANEL-LOGS", "打开运维日志查询页面")
    return render_template(
        "panel_logs.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/md/logs/panel")
def api_log_panel():
    """运维日志数据接口：汇总指标、五类图表与分页明细。"""
    payload = build_log_payload(
        keyword=(request.args.get("keyword") or "").strip(),
        source_code=(request.args.get("source_code") or "").strip(),
        type_code=(request.args.get("type_code") or "").strip(),
        dept_name=(request.args.get("dept_name") or "").strip(),
        result_code=(request.args.get("result_code") or "").strip(),
        date_from=(request.args.get("date_from") or "").strip(),
        date_to=(request.args.get("date_to") or "").strip(),
        page=int(request.args.get("page", 1)),
        size=int(request.args.get("size", 10)),
    )
    payload["result"] = "OK"
    return jsonify(payload)


@app.route("/api/md/logs/record", methods=["POST"])
def api_log_record():
    """运维日志登记接口：登记一条设备运维作业日志。"""
    payload = request.get_json(silent=True) or request.form.to_dict()
    outcome = save_log_record(
        (payload.get("asset_code") or "").strip(),
        (payload.get("log_source") or "").strip(),
        (payload.get("log_type") or "").strip(),
        (payload.get("happen_date") or "").strip(),
        payload.get("handler_name") or "",
        payload.get("content_text") or "",
        payload.get("cost_amount") or 0,
        payload.get("duration_hours") or 0,
        (payload.get("result_flag") or "DONE").strip(),
        operator="admin",
    )
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/md/logs/close", methods=["POST"])
def api_log_close():
    """运维日志闭环接口：更新某条日志的处理结果。"""
    payload = request.get_json(silent=True) or request.form.to_dict()
    outcome = close_log_record(
        int(payload.get("log_id") or 0),
        (payload.get("result_flag") or "").strip(),
        operator="admin",
    )
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/md/logs/export")
def api_log_export():
    """运维日志导出接口：按当前筛选条件生成 CSV 文本并登记导出批次。"""
    outcome = export_logbook_csv(
        keyword=(request.args.get("keyword") or "").strip(),
        source_code=(request.args.get("source_code") or "").strip(),
        type_code=(request.args.get("type_code") or "").strip(),
        dept_name=(request.args.get("dept_name") or "").strip(),
        result_code=(request.args.get("result_code") or "").strip(),
        date_from=(request.args.get("date_from") or "").strip(),
        date_to=(request.args.get("date_to") or "").strip(),
        operator="admin",
    )
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/md/logs/exports")
def api_log_exports():
    """导出批次留痕接口：返回最近的日志导出批次记录。"""
    return jsonify({"result": "OK", "batches": fetch_export_batches(8)})


@app.route("/api/md/journals/page")
def api_journal_page():
    """系统操作流水分页接口：按关键字与模块检索操作留痕。"""
    result = query_journal_page(
        keyword=(request.args.get("keyword") or "").strip(),
        module_name=(request.args.get("module_name") or "").strip(),
        action_type=(request.args.get("action_type") or "").strip(),
        date_from=(request.args.get("date_from") or "").strip(),
        date_to=(request.args.get("date_to") or "").strip(),
        page=int(request.args.get("page", 1)),
        size=int(request.args.get("size", 8)),
    )
    result["result"] = "OK"
    return jsonify(result)



# 【函数内插入】def bootstrap_application():
#    seed_demo_logbook()

# 【函数内插入】if __name__ == "__main__":
#    print("运维日志： http://127.0.0.1:%d/md/logs" % PORT)

# --------------------------------------------------------------------------
# 第 6 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/mdm_log.js                新增 409 行
#   [修改] static/mdm_theme.css             新增 21 行
#   [修改] templates/layout_mdm.html        新增 1 行
#   [新增] templates/panel_logs.html        新增 275 行
