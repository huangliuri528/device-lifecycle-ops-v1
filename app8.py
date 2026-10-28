# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-28（第 8 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 8 天新增与修改的源代码，不含第 1~7 天已有代码
#           本次迭代主题：计量校准管理模块 + 计量校准页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5001/md/overview）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 8 天新增业务模块 1：mdm_calib.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-28（第 8 天 / 共 15 天）
# 模块功能：计量校准管理模块。负责医疗器械强制检定与常规校准的计划编制、
#           校准执行登记、校准证书归档与证书到期预警，支持按设备关键字、
#           使用科室、校准类型、校准结论、计划状态与到期等级组合筛选并分页，
#           输出证书到期分布、校准类型分布、校准结论分布、科室强检完成率与
#           近 6 个月校准台次趋势等统计图表；校准结论为「合格」或「限用」时
#           按校准周期滚动生成下次校准日期，结论为「不合格」时不滚动并标记
#           停用待修，形成「计划 → 执行 → 出证 → 到期复检」的计量闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from mdm_dal import get_connection, write_ops_journal

# ------------------------------------------------------------------
# 校准类型字典：区分法定强制检定与院内自主校准，强检设备纳入完成率考核
# ------------------------------------------------------------------
CALIB_TYPE_DICT = {
    "FORCE": "强制检定",
    "ROUTINE": "常规校准",
    "ACCEPT": "验收校准",
    "REPAIR": "维修后校准",
}

# 校准结论字典：限用需登记限用范围，为不合格需停用待修
CALIB_RESULT_DICT = {
    "PASS": "合格",
    "LIMIT": "限用",
    "FAIL": "不合格",
}

# 校准计划状态字典：由登记与到期扫描共同维护
CALIB_STATE_DICT = {
    "PLAN": "待校准",
    "DONE": "已校准",
    "OVERDUE": "证书逾期",
    "STOP": "停用待修",
}

# 证书到期等级字典：按证书剩余有效天数划分，与维保到期等级相互独立
EXPIRE_LEVEL_DICT = {
    "OVERDUE": "已逾期",
    "URGENT": "15天内到期",
    "SOON": "30天内到期",
    "NORMAL": "有效期充足",
}

# 证书到期等级判定阈值（剩余天数由少到多匹配）
EXPIRE_LEVEL_THRESHOLD = (
    ("URGENT", 15),
    ("SOON", 30),
)

# 台账未配置校准周期时使用的兜底周期（天）
DEFAULT_CALIB_CYCLE = 365

# 强制检定默认周期（天），法定强检通常为一年一检
FORCE_CALIB_CYCLE = 365

# 分页默认条数
CALIB_PAGE_SIZE = 15

# 演示校准机构列表：出具证书的第三方计量机构
DEMO_AGENCY_LIST = (
    "省计量科学研究院",
    "市医疗器械检验所",
    "华测计量检测有限公司",
)


# ------------------------------------------------------------------
# 日期工具
# ------------------------------------------------------------------
def parse_date_text(text):
    """把 YYYY-MM-DD 文本解析为 date 对象，解析失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def date_to_text(value):
    """把 date 对象转换为 YYYY-MM-DD 文本，空值返回空串。"""
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d")


def today_date():
    """返回系统当前日期。"""
    return datetime.date.today()


def judge_expire_level(due_days):
    """按证书剩余有效天数判定到期等级编码。

    :param due_days: 剩余有效天数，负数代表已逾期
    :return: 到期等级编码，取值见 EXPIRE_LEVEL_DICT
    """
    if due_days is None:
        return "NORMAL"
    if due_days < 0:
        return "OVERDUE"
    for level_code, ceiling in EXPIRE_LEVEL_THRESHOLD:
        if due_days <= ceiling:
            return level_code
    return "NORMAL"


def calc_next_calib_date(calib_date, cycle_days):
    """按校准日期与周期天数推算下次校准日期文本。"""
    base = parse_date_text(calib_date) or today_date()
    try:
        cycle = int(cycle_days)
    except (TypeError, ValueError):
        cycle = DEFAULT_CALIB_CYCLE
    if cycle <= 0:
        cycle = DEFAULT_CALIB_CYCLE
    return date_to_text(base + datetime.timedelta(days=cycle))


def build_plan_code(asset_code, seq_no):
    """生成校准计划编号：CAL-年份-四位序号。"""
    return "CAL-%d-%04d" % (today_date().year, seq_no)


def build_record_code(asset_code, seq_no):
    """生成校准记录编号：CRD-年月日-三位序号。"""
    return "CRD-%s-%03d" % (today_date().strftime("%Y%m%d"), seq_no)


def fetch_asset_row(asset_code):
    """按资产编号读取台账记录，未找到返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT asset_id, asset_code, asset_name, asset_model, dept_name, "
            "asset_category, current_status, purchase_date FROM md_asset_ledger "
            "WHERE asset_code=?", (asset_code,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def next_plan_seq():
    """读取下一个校准计划序号，用于拼装计划编号。"""
    conn = get_connection()
    try:
        amount = conn.execute("SELECT COUNT(1) AS c FROM md_calib_plan").fetchone()["c"]
    finally:
        conn.close()
    return amount + 1


def next_record_seq():
    """读取下一个校准记录序号，用于拼装记录编号。"""
    conn = get_connection()
    try:
        amount = conn.execute("SELECT COUNT(1) AS c FROM md_calib_record").fetchone()["c"]
    finally:
        conn.close()
    return amount + 1


# ------------------------------------------------------------------
# 校准计划登记
# ------------------------------------------------------------------
def create_calib_plan(asset_code, calib_type="ROUTINE", cycle_days=0,
                      plan_date="", agency_name="", operator="admin"):
    """为一台设备登记一条计量校准计划。

    :param asset_code: 设备资产编号
    :param calib_type: 校准类型编码，见 CALIB_TYPE_DICT
    :param cycle_days: 校准周期天数，为 0 时按类型取默认值
    :param plan_date: 计划校准日期，留空取当天
    :param agency_name: 委外校准机构名称
    :param operator: 登记人
    :return: 含 success 与 message 的执行结果字典
    """
    asset_code = (asset_code or "").strip()
    if not asset_code:
        return {"success": False, "message": "设备资产编号不能为空"}
    asset_row = fetch_asset_row(asset_code)
    if asset_row is None:
        return {"success": False, "message": "资产编号 %s 不存在" % asset_code}
    if calib_type not in CALIB_TYPE_DICT:
        return {"success": False, "message": "校准类型 %s 不合法" % calib_type}
    try:
        cycle = int(cycle_days)
    except (TypeError, ValueError):
        cycle = 0
    if cycle < 0:
        return {"success": False, "message": "校准周期不能为负数"}
    if cycle == 0:
        cycle = FORCE_CALIB_CYCLE if calib_type == "FORCE" else DEFAULT_CALIB_CYCLE
    base_date = parse_date_text(plan_date) or today_date()
    next_date = date_to_text(base_date + datetime.timedelta(days=cycle))

    conn = get_connection()
    try:
        exist = conn.execute(
            "SELECT plan_id FROM md_calib_plan WHERE asset_code=? "
            "AND calib_type=?", (asset_code, calib_type)).fetchone()
        if exist is not None:
            return {"success": False,
                    "message": "该设备已存在同类型校准计划，不可重复登记"}
        plan_code = build_plan_code(asset_code, next_plan_seq())
        conn.execute(
            "INSERT INTO md_calib_plan "
            "(plan_code, asset_code, asset_name, dept_name, asset_category, "
            "calib_type, calib_cycle, plan_date, next_calib_at, agency_name, "
            "plan_state, calib_amount, operator_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (plan_code, asset_code, asset_row["asset_name"],
             asset_row["dept_name"], asset_row["asset_category"], calib_type,
             cycle, date_to_text(base_date), next_date,
             (agency_name or "").strip(), "PLAN", 0, operator))
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("计量校准", "登记", plan_code,
                      "登记 %s 校准计划，周期 %d 天，下次校准 %s"
                      % (asset_code, cycle, next_date), operator)
    return {"success": True, "message": "校准计划登记成功，编号 %s" % plan_code,
            "plan_code": plan_code}


# ------------------------------------------------------------------
# 校准执行登记
# ------------------------------------------------------------------
def save_calib_record(plan_code, cert_no, calib_date, result_code, agency_name,
                      agency_qual="", calib_person="", calib_fee=0,
                      limit_scope="", operator="admin"):
    """登记一次校准执行结果并归档证书信息。

    结论为「合格」或「限用」时按周期滚动生成下次校准日期；结论为「不合格」
    时不滚动，计划状态置为停用待修，设备须修复后重新校准。

    :return: 含 success 与 message 的执行结果字典
    """
    plan_code = (plan_code or "").strip()
    if not plan_code:
        return {"success": False, "message": "校准计划编号不能为空"}
    if result_code not in CALIB_RESULT_DICT:
        return {"success": False, "message": "校准结论 %s 不合法" % result_code}
    if not (cert_no or "").strip():
        return {"success": False, "message": "校准证书编号不能为空"}
    day = parse_date_text(calib_date)
    if day is None:
        return {"success": False, "message": "校准日期格式应为 YYYY-MM-DD"}
    if day > today_date():
        return {"success": False, "message": "校准日期不能晚于今天"}
    if not (calib_person or "").strip():
        return {"success": False, "message": "校准/送检责任人不能为空"}
    try:
        fee = float(calib_fee)
    except (TypeError, ValueError):
        return {"success": False, "message": "校准费用必须为数字"}
    if fee < 0:
        return {"success": False, "message": "校准费用不能为负数"}
    if result_code == "LIMIT" and not (limit_scope or "").strip():
        return {"success": False, "message": "结论为限用时必须填写限用范围"}

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_calib_plan WHERE plan_code=?", (plan_code,)).fetchone()
        if row is None:
            return {"success": False, "message": "校准计划 %s 不存在" % plan_code}
        plan = dict(row)
        if plan["plan_state"] == "STOP":
            return {"success": False, "message": "该计划已停用待修，请先修复设备"}
        valid_until = calc_next_calib_date(date_to_text(day), plan["calib_cycle"])
        record_code = build_record_code(plan["asset_code"], next_record_seq())
        conn.execute(
            "INSERT INTO md_calib_record "
            "(record_code, plan_code, asset_code, asset_name, dept_name, "
            "calib_type, cert_no, agency_name, agency_qual, calib_date, "
            "valid_until, result_code, limit_scope, calib_fee, calib_person) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record_code, plan_code, plan["asset_code"], plan["asset_name"],
             plan["dept_name"], plan["calib_type"], cert_no.strip(),
             (agency_name or "").strip(), (agency_qual or "").strip(),
             date_to_text(day), valid_until, result_code,
             (limit_scope or "").strip(), round(fee, 2), calib_person.strip()))
        new_state = "STOP" if result_code == "FAIL" else "DONE"
        conn.execute(
            "UPDATE md_calib_plan SET plan_state=?, last_calib_at=?, "
            "next_calib_at=?, cert_no=?, agency_name=?, "
            "calib_amount=calib_amount+1 WHERE plan_code=?",
            (new_state, date_to_text(day), valid_until, cert_no.strip(),
             (agency_name or "").strip() or plan["agency_name"], plan_code))
        conn.commit()
    finally:
        conn.close()
    tip = "，已停用待修" if result_code == "FAIL" else "，下次校准 %s" % valid_until
    write_ops_journal("计量校准", "执行", plan_code,
                      "登记校准结果：%s%s"
                      % (CALIB_RESULT_DICT[result_code], tip), operator)
    return {"success": True,
            "message": "校准结果登记成功，编号 %s%s" % (record_code, tip),
            "record_code": record_code}


# ------------------------------------------------------------------
# 证书到期扫描
# ------------------------------------------------------------------
def scan_expire_certificates():
    """扫描全部校准计划，把证书已过期且仍在校准中的计划置为逾期状态。

    :return: 本次被判定为逾期的计划条数
    """
    conn = get_connection()
    amount = 0
    try:
        rows = conn.execute(
            "SELECT plan_code, next_calib_at, plan_state FROM md_calib_plan "
            "WHERE plan_state IN ('PLAN','DONE')").fetchall()
        for item in rows:
            due = parse_date_text(item["next_calib_at"])
            if due is None or due >= today_date():
                continue
            conn.execute(
                "UPDATE md_calib_plan SET plan_state='OVERDUE' WHERE plan_code=?",
                (item["plan_code"],))
            amount += 1
        if amount:
            conn.commit()
    finally:
        conn.close()
    if amount:
        write_ops_journal("计量校准", "扫描", "CALIB-EXPIRE-SCAN",
                          "证书到期扫描完成，新增逾期计划 %d 条" % amount)
    return amount


# ------------------------------------------------------------------
# 查询与统计
# ------------------------------------------------------------------
def fetch_calib_plans(keyword="", dept_name="", calib_type="", plan_state="",
                      expire_level="", page=1):
    """按组合条件分页查询校准计划，并附带到期等级与剩余天数。"""
    where = []
    params = []
    if keyword:
        where.append("(asset_code LIKE ? OR asset_name LIKE ? OR cert_no LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like, like])
    if dept_name:
        where.append("dept_name=?")
        params.append(dept_name)
    if calib_type:
        where.append("calib_type=?")
        params.append(calib_type)
    if plan_state:
        where.append("plan_state=?")
        params.append(plan_state)
    today = today_date()
    if expire_level and expire_level in EXPIRE_LEVEL_DICT:
        # 到期等级在 SQL 层按证书有效期区间过滤，保证分页总数与筛选结果一致；
        # 停用待修设备已暂停计量，不参与到期等级统计
        where.append("plan_state<>'STOP'")
        if expire_level == "OVERDUE":
            where.append("next_calib_at<>'' AND next_calib_at<?")
            params.append(date_to_text(today))
        elif expire_level == "URGENT":
            where.append("next_calib_at>=? AND next_calib_at<=?")
            params.append(date_to_text(today))
            params.append(date_to_text(today + datetime.timedelta(days=15)))
        elif expire_level == "SOON":
            where.append("next_calib_at>? AND next_calib_at<=?")
            params.append(date_to_text(today + datetime.timedelta(days=15)))
            params.append(date_to_text(today + datetime.timedelta(days=30)))
        else:
            where.append("next_calib_at>?")
            params.append(date_to_text(today + datetime.timedelta(days=30)))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    if page_no < 1:
        page_no = 1
    offset = (page_no - 1) * CALIB_PAGE_SIZE

    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_calib_plan" + clause, params).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM md_calib_plan" + clause +
            " ORDER BY next_calib_at ASC, plan_id DESC LIMIT ? OFFSET ?",
            params + [CALIB_PAGE_SIZE, offset]).fetchall()
    finally:
        conn.close()

    today = today_date()
    records = []
    for item in rows:
        row = dict(item)
        due = parse_date_text(row["next_calib_at"])
        row["due_days"] = (due - today).days if due else None
        row["expire_level"] = judge_expire_level(row["due_days"])
        row["expire_label"] = EXPIRE_LEVEL_DICT[row["expire_level"]]
        row["type_label"] = CALIB_TYPE_DICT.get(row["calib_type"], row["calib_type"])
        row["state_label"] = CALIB_STATE_DICT.get(row["plan_state"], row["plan_state"])
        if expire_level and row["expire_level"] != expire_level:
            continue
        records.append(row)
    return {
        "records": records,
        "total": total,
        "page": page_no,
        "page_size": CALIB_PAGE_SIZE,
        "page_amount": max(1, (total + CALIB_PAGE_SIZE - 1) // CALIB_PAGE_SIZE),
    }


def fetch_calib_history(asset_code="", limit=50):
    """按设备读取历史校准记录，默认返回最近 50 条。"""
    sql = ("SELECT * FROM md_calib_record")
    params = []
    if asset_code:
        sql += " WHERE asset_code=?"
        params.append(asset_code)
    sql += " ORDER BY calib_date DESC, record_id DESC LIMIT ?"
    params.append(limit)
    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    result = []
    for item in rows:
        row = dict(item)
        row["type_label"] = CALIB_TYPE_DICT.get(row["calib_type"], row["calib_type"])
        row["result_label"] = CALIB_RESULT_DICT.get(row["result_code"], row["result_code"])
        result.append(row)
    return result


def build_calib_summary():
    """汇总计量校准核心指标：计划量、逾期量、本月到期、合格率与强检完成率。"""
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(1) AS c FROM md_calib_plan").fetchone()["c"]
        overdue = conn.execute(
            "SELECT COUNT(1) AS c FROM md_calib_plan WHERE plan_state='OVERDUE'"
        ).fetchone()["c"]
        stop = conn.execute(
            "SELECT COUNT(1) AS c FROM md_calib_plan WHERE plan_state='STOP'"
        ).fetchone()["c"]
        done = conn.execute(
            "SELECT COUNT(1) AS c FROM md_calib_plan WHERE plan_state='DONE'"
        ).fetchone()["c"]
        force_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_calib_plan WHERE calib_type='FORCE'"
        ).fetchone()["c"]
        force_done = conn.execute(
            "SELECT COUNT(1) AS c FROM md_calib_plan WHERE calib_type='FORCE' "
            "AND plan_state IN ('DONE','STOP')").fetchone()["c"]
        pass_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM md_calib_record WHERE result_code='PASS'"
        ).fetchone()["c"]
        record_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM md_calib_record").fetchone()["c"]
        fee_row = conn.execute(
            "SELECT COALESCE(SUM(calib_fee),0) AS s FROM md_calib_record").fetchone()
    finally:
        conn.close()

    today = today_date()
    month_tail = (today + datetime.timedelta(days=30))
    month_amount = 0
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT next_calib_at FROM md_calib_plan "
            "WHERE plan_state IN ('PLAN','DONE','OVERDUE')").fetchall()
    finally:
        conn.close()
    for item in rows:
        due = parse_date_text(item["next_calib_at"])
        if due and today <= due <= month_tail:
            month_amount += 1

    return {
        "total_plan": total,
        "overdue_amount": overdue,
        "stop_amount": stop,
        "done_amount": done,
        "month_amount": month_amount,
        "pass_rate": round(pass_amount * 100.0 / record_amount, 1) if record_amount else 0.0,
        "force_cover_rate": round(force_done * 100.0 / force_total, 1) if force_total else 0.0,
        "force_total": force_total,
        "record_amount": record_amount,
        "fee_total": round(fee_row["s"], 2),
    }


def build_calib_charts():
    """构建计量校准五类图表数据：到期分布、类型分布、结论分布、科室完成率与月度趋势。"""
    today = today_date()
    conn = get_connection()
    try:
        plan_rows = conn.execute(
            "SELECT dept_name, calib_type, plan_state, next_calib_at "
            "FROM md_calib_plan").fetchall()
        record_rows = conn.execute(
            "SELECT dept_name, calib_type, result_code, calib_date "
            "FROM md_calib_record").fetchall()
    finally:
        conn.close()

    expire_counter = {}
    for code in EXPIRE_LEVEL_DICT:
        expire_counter[code] = 0
    for item in plan_rows:
        if item["plan_state"] == "STOP":
            continue
        due = parse_date_text(item["next_calib_at"])
        due_days = (due - today).days if due else None
        expire_counter[judge_expire_level(due_days)] += 1
    expire_distribution = [
        {"name": EXPIRE_LEVEL_DICT[code], "value": expire_counter[code]}
        for code in ("OVERDUE", "URGENT", "SOON", "NORMAL")
    ]

    type_counter = {}
    for item in plan_rows:
        type_counter[item["calib_type"]] = type_counter.get(item["calib_type"], 0) + 1
    type_distribution = [
        {"name": CALIB_TYPE_DICT.get(code, code), "value": amount}
        for code, amount in type_counter.items()
    ]

    result_counter = {}
    for item in record_rows:
        result_counter[item["result_code"]] = result_counter.get(item["result_code"], 0) + 1
    result_distribution = [
        {"name": CALIB_RESULT_DICT.get(code, code), "value": amount}
        for code, amount in result_counter.items()
    ]

    dept_counter = {}
    dept_done = {}
    for item in plan_rows:
        dept = item["dept_name"] or "未分配"
        dept_counter[dept] = dept_counter.get(dept, 0) + 1
        if item["plan_state"] in ("DONE", "STOP"):
            dept_done[dept] = dept_done.get(dept, 0) + 1
    dept_progress = []
    for dept, amount in dept_counter.items():
        rate = round(dept_done.get(dept, 0) * 100.0 / amount, 1) if amount else 0.0
        dept_progress.append({"name": dept, "rate": rate, "total": amount})
    dept_progress.sort(key=lambda x: x["rate"], reverse=True)

    month_keys = []
    cursor = today.replace(day=1)
    for _ in range(6):
        month_keys.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - datetime.timedelta(days=1)).replace(day=1)
    month_keys.reverse()
    month_counter = {key: 0 for key in month_keys}
    for item in record_rows:
        day = parse_date_text(item["calib_date"])
        if day is None:
            continue
        key = day.strftime("%Y-%m")
        if key in month_counter:
            month_counter[key] += 1

    return {
        "expire_distribution": expire_distribution,
        "type_distribution": type_distribution,
        "result_distribution": result_distribution,
        "dept_progress": dept_progress,
        "month_trend": {
            "months": month_keys,
            "values": [month_counter[key] for key in month_keys],
        },
    }


def fetch_calib_options():
    """校准管理下拉选项：科室列表、字典项与设备编号候选。"""
    conn = get_connection()
    try:
        dept_rows = conn.execute(
            "SELECT DISTINCT dept_name FROM md_calib_plan "
            "WHERE dept_name<>'' ORDER BY dept_name").fetchall()
        asset_rows = conn.execute(
            "SELECT asset_code, asset_name FROM md_asset_ledger "
            "WHERE current_status<>'SCRAP' ORDER BY asset_code").fetchall()
    finally:
        conn.close()
    return {
        "dept_list": [dict(item)["dept_name"] for item in dept_rows],
        "asset_list": [dict(item) for item in asset_rows],
        "type_options": [{"code": k, "label": v} for k, v in CALIB_TYPE_DICT.items()],
        "state_options": [{"code": k, "label": v} for k, v in CALIB_STATE_DICT.items()],
        "result_options": [{"code": k, "label": v} for k, v in CALIB_RESULT_DICT.items()],
        "expire_options": [{"code": k, "label": v} for k, v in EXPIRE_LEVEL_DICT.items()],
    }


def build_calib_payload(keyword="", dept_name="", calib_type="", plan_state="",
                        expire_level="", page=1):
    """组装计量校准页面所需的全部数据：汇总指标、五类图表、分页明细与下拉选项。"""
    scan_expire_certificates()
    pager = fetch_calib_plans(keyword, dept_name, calib_type, plan_state,
                              expire_level, page)
    return {
        "summary": build_calib_summary(),
        "charts": build_calib_charts(),
        "records": pager["records"],
        "total": pager["total"],
        "page": pager["page"],
        "page_amount": pager["page_amount"],
        "options": fetch_calib_options(),
        "history": fetch_calib_history(limit=12),
        "result": "OK",
    }


def build_calib_brief():
    """计量校准看板摘要，供运维总览看板嵌入。"""
    summary = build_calib_summary()
    return {
        "total_plan": summary["total_plan"],
        "overdue_amount": summary["overdue_amount"],
        "month_amount": summary["month_amount"],
        "pass_rate": summary["pass_rate"],
        "force_cover_rate": summary["force_cover_rate"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_calib():
    """灌入计量校准演示数据：为台账设备编制校准计划并登记历史校准记录。

    :return: 生成的校准计划条数
    """
    conn = get_connection()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM md_calib_plan").fetchone()["c"]
        if already:
            return 0
        rows = conn.execute(
            "SELECT asset_code, asset_name, dept_name, asset_category "
            "FROM md_asset_ledger WHERE current_status<>'SCRAP' "
            "ORDER BY asset_id").fetchall()
        assets = [dict(item) for item in rows]
    finally:
        conn.close()
    if not assets:
        return 0

    today = today_date()
    type_cycle = (
        ("FORCE", 365, 0),
        ("ROUTINE", 365, 1),
        ("ACCEPT", 730, 2),
        ("REPAIR", 180, 3),
    )
    amount = 0
    for index, asset in enumerate(assets):
        if index >= 8:
            break
        calib_type, cycle, offset = type_cycle[index % len(type_cycle)]
        # 计划日期往前推，使演示数据的证书到期分布覆盖四个等级
        plan_day = today - datetime.timedelta(days=cycle - offset * 46)
        result = create_calib_plan(
            asset["asset_code"], calib_type, cycle,
            date_to_text(plan_day),
            DEMO_AGENCY_LIST[index % len(DEMO_AGENCY_LIST)], "医工科-周工")
        if not result.get("success"):
            continue
        amount += 1
        plan_code = result["plan_code"]
        if index % 8 == 0:
            # 第 1 台：证书已逾期，尚未复检
            save_calib_record(
                plan_code, "JLZ-%d-%04d" % (today.year - 1, 101),
                date_to_text(today - datetime.timedelta(days=cycle + 20)),
                "PASS", DEMO_AGENCY_LIST[0], "CNAS-L-%04d" % (1000 + index),
                "医工科-周工", 1200 + index * 60)
            scan_expire_certificates()
        elif index % 8 == 1:
            # 第 2 台：15 天内即将到期
            save_calib_record(
                plan_code, "JLZ-%d-%04d" % (today.year, 202),
                date_to_text(today - datetime.timedelta(days=cycle - 8)),
                "PASS", DEMO_AGENCY_LIST[1], "CNAS-L-%04d" % (1000 + index),
                "医工科-周工", 860 + index * 40)
        elif index % 8 == 2:
            # 第 3 台：结论限用，登记限用范围
            save_calib_record(
                plan_code, "JLZ-%d-%04d" % (today.year, 303),
                date_to_text(today - datetime.timedelta(days=40)),
                "LIMIT", DEMO_AGENCY_LIST[2], "CNAS-L-%04d" % (1000 + index),
                "医工科-李工", 1500 + index * 30,
                "仅限 0~100 mL/min 流量段使用，超出段位误差超差")
        elif index % 6 == 3:
            # 第 4 台：结论不合格，计划转为停用待修
            save_calib_record(
                plan_code, "JLZ-%d-%04d" % (today.year, 404),
                date_to_text(today - datetime.timedelta(days=12)),
                "FAIL", DEMO_AGENCY_LIST[0], "CNAS-L-%04d" % (1000 + index),
                "医工科-李工", 900 + index * 25)
        elif index % 6 == 4:
            # 第 5 台：强检已按期完成，有效期充足
            save_calib_record(
                plan_code, "JLZ-%d-%04d" % (today.year, 505),
                date_to_text(today - datetime.timedelta(days=30)),
                "PASS", DEMO_AGENCY_LIST[1], "CNAS-L-%04d" % (1000 + index),
                "医工科-周工", 1100 + index * 45)
        elif index % 6 == 5:
            # 第 6 台：三次历史校准，形成近半年趋势样本
            for step in range(3):
                save_calib_record(
                    plan_code, "JLZ-%d-%04d" % (today.year, 600 + step),
                    date_to_text(today - datetime.timedelta(days=150 - step * 55)),
                    "PASS", DEMO_AGENCY_LIST[step % len(DEMO_AGENCY_LIST)],
                    "CNAS-L-%04d" % (1000 + index), "医工科-李工", 980 + step * 120)
    return amount

# --------------------------------------------------------------------------
# 第 8 天数据表脚本增量：mdm_schema.sql（M，已内联为字符串常量 DAY8_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY8_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 8 天新增：设备计量校准计划表
-- ==================================================================
-- 校准计划表：按「一台设备一种校准类型一条」编制计量校准计划，区分法定
-- 强制检定与院内常规校准，保存校准周期、下次校准日期、当前证书编号与计划
-- 状态，支撑证书到期预警与强检完成率考核
CREATE TABLE IF NOT EXISTS md_calib_plan (
    plan_id        INTEGER PRIMARY KEY AUTOINCREMENT,  -- 计划主键
    plan_code      TEXT    DEFAULT '',                 -- 计划编号（CAL-年份-序号）
    asset_code     TEXT    NOT NULL,                   -- 资产编号
    asset_name     TEXT    DEFAULT '',                 -- 设备名称快照
    dept_name      TEXT    DEFAULT '',                 -- 使用科室快照
    asset_category TEXT    DEFAULT '',                 -- 设备分类快照
    calib_type     TEXT    DEFAULT 'ROUTINE',          -- 校准类型（见 CALIB_TYPE_DICT）
    calib_cycle    INTEGER DEFAULT 365,                -- 校准周期（天）
    plan_date      TEXT    DEFAULT '',                 -- 计划校准日期
    last_calib_at  TEXT    DEFAULT '',                 -- 最近一次校准日期
    next_calib_at  TEXT    DEFAULT '',                 -- 下次校准日期（证书有效期至）
    cert_no        TEXT    DEFAULT '',                 -- 当前有效证书编号
    agency_name    TEXT    DEFAULT '',                 -- 委外校准机构名称
    plan_state     TEXT    DEFAULT 'PLAN',             -- 计划状态（见 CALIB_STATE_DICT）
    calib_amount   INTEGER DEFAULT 0,                  -- 累计校准次数
    operator_name  TEXT    DEFAULT 'admin',            -- 计划登记人
    created_at     TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 8 天新增：设备计量校准记录表
-- ==================================================================
-- 校准记录表：按「一次校准一条」归档校准执行结果与证书信息，含证书编号、
-- 校准机构资质、有效期、校准结论、限用范围与校准费用，结论为合格或限用时
-- 滚动生成下次校准日期，结论为不合格时计划转为停用待修
CREATE TABLE IF NOT EXISTS md_calib_record (
    record_id    INTEGER PRIMARY KEY AUTOINCREMENT,   -- 记录主键
    record_code  TEXT    DEFAULT '',                  -- 记录编号（CRD-日期-序号）
    plan_code    TEXT    DEFAULT '',                  -- 关联校准计划编号
    asset_code   TEXT    NOT NULL,                    -- 资产编号
    asset_name   TEXT    DEFAULT '',                  -- 设备名称快照
    dept_name    TEXT    DEFAULT '',                  -- 使用科室快照
    calib_type   TEXT    DEFAULT 'ROUTINE',           -- 校准类型快照
    cert_no      TEXT    DEFAULT '',                  -- 校准证书编号
    agency_name  TEXT    DEFAULT '',                  -- 校准机构名称
    agency_qual  TEXT    DEFAULT '',                  -- 校准机构资质编号
    calib_date   TEXT    DEFAULT '',                  -- 校准日期
    valid_until  TEXT    DEFAULT '',                  -- 证书有效期至
    result_code  TEXT    DEFAULT 'PASS',              -- 校准结论（见 CALIB_RESULT_DICT）
    limit_scope  TEXT    DEFAULT '',                  -- 限用范围（结论为限用时必填）
    calib_fee    REAL    DEFAULT 0,                   -- 校准费用（元）
    calib_person TEXT    DEFAULT '',                  -- 送检/校准责任人
    created_at   TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_calibplan_code  ON md_calib_plan (plan_code);
CREATE INDEX IF NOT EXISTS idx_calibplan_asset ON md_calib_plan (asset_code);
CREATE INDEX IF NOT EXISTS idx_calibplan_type  ON md_calib_plan (calib_type);
CREATE INDEX IF NOT EXISTS idx_calibplan_state ON md_calib_plan (plan_state);
CREATE INDEX IF NOT EXISTS idx_calibplan_next  ON md_calib_plan (next_calib_at);
CREATE INDEX IF NOT EXISTS idx_calibrec_asset  ON md_calib_record (asset_code);
CREATE INDEX IF NOT EXISTS idx_calibrec_date   ON md_calib_record (calib_date);
CREATE INDEX IF NOT EXISTS idx_calibrec_result ON md_calib_record (result_code);
"""


def apply_day8_schema(conn):
    """把第 8 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY8_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 8 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-28（第 8 天 / 共 15 天）
#           分析、盘点维保管理、运维日志查询、设备报废审批与计量校准管理
#           八套页面路由，并提供台账增删改查、闲置识别扫描、状态切换、ROI
#           测算与年度收支档案维护、盘点任务生成与账实核对、维保登记、运维
#           日志检索登记闭环与 CSV 导出、报废申请发起逐级审批流转与处置归档，
#           以及校准计划编制、校准结果登记、证书到期扫描与校准档案查询等
#           RESTful 接口，供前端表格、弹窗与 ECharts 图表异步取数

from mdm_calib import (
    build_calib_payload,
    build_calib_brief,
    create_calib_plan,
    save_calib_record,
    scan_expire_certificates,
    fetch_calib_history,
    seed_demo_calib,
    CALIB_TYPE_DICT,
    CALIB_STATE_DICT,
)

# 【函数内插入】def api_overview():
#    payload["calib_brief"] = build_calib_brief()

@app.route("/md/calib")
def panel_calib():
    """计量校准管理页面：校准概览、五类分析图表、计划明细与校准档案。"""
    write_ops_journal("计量校准", "查询", "PANEL-CALIB", "打开计量校准管理页面")
    return render_template(
        "panel_calib.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/md/calib/panel")
def api_calib_panel():
    """计量校准数据接口：汇总指标、五类图表、分页明细、最近档案与下拉选项。"""
    payload = build_calib_payload(
        keyword=(request.args.get("keyword") or "").strip(),
        dept_name=(request.args.get("dept_name") or "").strip(),
        calib_type=(request.args.get("calib_type") or "").strip(),
        plan_state=(request.args.get("plan_state") or "").strip(),
        expire_level=(request.args.get("expire_level") or "").strip(),
        page=int(request.args.get("page", 1)),
    )
    return jsonify(payload)


@app.route("/api/md/calib/plan", methods=["POST"])
def api_calib_plan():
    """编制一条计量校准计划。"""
    body = request.get_json(silent=True) or {}
    result = create_calib_plan(
        (body.get("asset_code") or "").strip(),
        (body.get("calib_type") or "ROUTINE").strip(),
        body.get("cycle_days", 0),
        (body.get("plan_date") or "").strip(),
        (body.get("agency_name") or "").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/calib/record", methods=["POST"])
def api_calib_record():
    """登记一次校准执行结果并归档证书，合格或限用滚动生成下次校准日期。"""
    body = request.get_json(silent=True) or {}
    result = save_calib_record(
        (body.get("plan_code") or "").strip(),
        (body.get("cert_no") or "").strip(),
        (body.get("calib_date") or "").strip(),
        (body.get("result_code") or "").strip(),
        (body.get("agency_name") or "").strip(),
        (body.get("agency_qual") or "").strip(),
        (body.get("calib_person") or "").strip(),
        body.get("calib_fee", 0),
        (body.get("limit_scope") or "").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/calib/history")
def api_calib_history():
    """查询某台设备的历史校准记录档案。"""
    return jsonify({
        "result": "OK",
        "records": fetch_calib_history(
            (request.args.get("asset_code") or "").strip()),
    })


@app.route("/api/md/calib/scan", methods=["POST"])
def api_calib_scan():
    """手动触发一次证书到期扫描，返回新增逾期计划条数。"""
    return jsonify({
        "result": "OK",
        "amount": scan_expire_certificates(),
    })



# 【函数内插入】def bootstrap_application():
#    seed_demo_calib()

# 【函数内插入】if __name__ == "__main__":
#    print("计量校准： http://127.0.0.1:%d/md/calib" % PORT)

# --------------------------------------------------------------------------
# 第 8 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/mdm_calib.js              新增 395 行
#   [修改] templates/layout_mdm.html        新增 1 行
#   [新增] templates/panel_calib.html       新增 295 行
