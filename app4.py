# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-24（第 4 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 4 天新增与修改的源代码，不含第 1~3 天已有代码
#           本次迭代主题：设备ROI投入产出回报计算模块 + ROI分析图表
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5001/md/overview）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 4 天新增业务模块 1：mdm_roi.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-24（第 4 天 / 共 15 天）
# 模块功能：设备 ROI 投入产出回报计算模块。负责设备年度收支档案维护、
#           采购原值折旧摊销、年度净收益与投资回报率测算、回本进度与
#           回本年限预估、盈亏分级判定，并输出 ROI 排行榜、盈亏结构
#           占比、科室回报对比等分析图表所需的数据集合
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime
from mdm_settings import ASSET_STATUS_DICT, ASSET_CATEGORY_DICT
from mdm_dal import get_connection, write_ops_journal

# 回报等级字典：按投资回报率区间划分设备创收能力等级
ROI_LEVEL_DICT = {
    "EXCELLENT": "优质回报",
    "GOOD": "良好回报",
    "NORMAL": "收支平衡",
    "LOSS": "亏损运行",
}

# 等级判定阈值（按 ROI 百分比由高到低匹配）
ROI_LEVEL_THRESHOLD = [
    ("EXCELLENT", 30.0),
    ("GOOD", 10.0),
    ("NORMAL", 0.0),
    ("LOSS", -9999.0),
]

# 年度天数换算基数，用于把已使用天数折算为年数
DAYS_PER_YEAR = 365.25


def today_text():
    """返回当前日期文本，格式 YYYY-MM-DD。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def calc_used_years(purchase_date):
    """按采购日期计算设备已投入使用年限，保留一位小数。

    采购日期为空或格式非法时按 0 年处理，保证测算不中断。
    """
    if not purchase_date:
        return 0.0
    try:
        start = datetime.datetime.strptime(str(purchase_date)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 0.0
    days = (datetime.date.today() - start).days
    return round(max(days, 0) / DAYS_PER_YEAR, 1)


def calc_depreciation_yearly(purchase_price, service_life):
    """按直线法计算年度折旧额。

    预计使用年限为 0 或为空时，按 10 年兜底折旧，避免除零错误。
    """
    price = float(purchase_price or 0)
    life = int(service_life or 0)
    if life <= 0:
        life = 10
    return round(price / life, 2)


def judge_roi_level(roi_percent):
    """按投资回报率匹配回报等级编码与名称。"""
    for level_code, floor in ROI_LEVEL_THRESHOLD:
        if roi_percent >= floor:
            return level_code, ROI_LEVEL_DICT.get(level_code, "未分级")
    return "LOSS", ROI_LEVEL_DICT["LOSS"]


def evaluate_single_roi(asset_row, profile_row):
    """单台设备 ROI 测算核心算法。

    计算链路：年度折旧 = 采购原值 / 预计使用年限
              年度净收益 = 年度创收 - 年度维保支出 - 年度其他支出 - 年度折旧
              投资回报率 = 年度净收益 / 采购原值 × 100%
              累计净收益 = 年度净收益 × 已使用年限
              预计回本年限 = 采购原值 / 年度净收益（净收益为正时有效）
    """
    invest = float(asset_row.get("purchase_price") or 0)
    life = int(asset_row.get("service_life") or 0)
    used_years = calc_used_years(asset_row.get("purchase_date"))
    depreciate = calc_depreciation_yearly(invest, life)

    income = float((profile_row or {}).get("annual_income") or 0)
    maintain_cost = float((profile_row or {}).get("annual_maintain_cost") or 0)
    other_cost = float((profile_row or {}).get("annual_other_cost") or 0)

    cost_total = maintain_cost + other_cost + depreciate
    net_year = round(income - cost_total, 2)
    roi_percent = round(net_year / invest * 100, 2) if invest > 0 else 0.0
    profit_flag = 1 if net_year > 0 else 0
    level_code, level_name = judge_roi_level(roi_percent)

    accumulate = round(net_year * used_years, 2)
    payback_years = round(invest / net_year, 1) if net_year > 0 else 0.0
    remain_years = round(max(life - used_years, 0), 1) if life > 0 else 0.0
    payback_progress = round(accumulate / invest * 100, 2) if invest > 0 else 0.0

    return {
        "asset_id": asset_row.get("asset_id"),
        "asset_code": asset_row.get("asset_code"),
        "asset_name": asset_row.get("asset_name"),
        "asset_category": asset_row.get("asset_category"),
        "category_label": ASSET_CATEGORY_DICT.get(
            asset_row.get("asset_category"), "其他"),
        "dept_name": asset_row.get("dept_name") or "未分配",
        "current_status": asset_row.get("current_status"),
        "status_label": ASSET_STATUS_DICT.get(
            asset_row.get("current_status"), "未知"),
        "invest_amount": invest,
        "service_life": life,
        "used_years": used_years,
        "remain_years": remain_years,
        "depreciate_year": depreciate,
        "annual_income": income,
        "annual_maintain_cost": maintain_cost,
        "annual_other_cost": other_cost,
        "annual_cost_total": round(cost_total, 2),
        "net_profit_year": net_year,
        "accumulate_profit": accumulate,
        "roi_percent": roi_percent,
        "payback_years": payback_years,
        "payback_progress": payback_progress,
        "profit_flag": profit_flag,
        "level_code": level_code,
        "level_name": level_name,
        "stat_year": (profile_row or {}).get("stat_year") or "",
    }


def fetch_roi_profile_map():
    """读取全部设备年度收支档案，返回以资产编号为键的字典。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT asset_code, annual_income, annual_maintain_cost, "
            "annual_other_cost, stat_year FROM md_roi_profile"
        ).fetchall()
    finally:
        conn.close()
    return {r["asset_code"]: dict(r) for r in rows}


def fetch_all_assets_for_roi():
    """取全部参与 ROI 测算的设备台账（已报废设备仍纳入，便于完整核算）。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT asset_id, asset_code, asset_name, asset_category, dept_name, "
            "purchase_date, purchase_price, service_life, current_status "
            "FROM md_asset_ledger ORDER BY asset_id"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def build_roi_rows():
    """按台账逐台测算 ROI，返回测算明细列表。"""
    profile_map = fetch_roi_profile_map()
    return [evaluate_single_roi(a, profile_map.get(a["asset_code"]))
            for a in fetch_all_assets_for_roi()]


def summarize_roi(rows):
    """对测算明细做汇总：盈亏台数、总额、平均回报率、等级分布。"""
    profit_amount = sum(1 for r in rows if r["profit_flag"] == 1)
    loss_amount = len(rows) - profit_amount
    total_invest = round(sum(r["invest_amount"] for r in rows), 2)
    total_income = round(sum(r["annual_income"] for r in rows), 2)
    total_net = round(sum(r["net_profit_year"] for r in rows), 2)
    avg_roi = round(sum(r["roi_percent"] for r in rows) / len(rows), 2) if rows else 0.0
    level_rows = []
    for code, name in ROI_LEVEL_DICT.items():
        amount = sum(1 for r in rows if r["level_code"] == code)
        level_rows.append({
            "level_code": code,
            "level_name": name,
            "amount": amount,
        })
    return {
        "device_amount": len(rows),
        "profit_amount": profit_amount,
        "loss_amount": loss_amount,
        "profit_rate": round(profit_amount / len(rows) * 100, 2) if rows else 0.0,
        "total_invest": total_invest,
        "total_income": total_income,
        "total_net_year": total_net,
        "avg_roi_percent": avg_roi,
        "level_distribution": level_rows,
    }


def build_dept_roi_summary(rows):
    """按科室聚合投资回报，用于科室横向对比图表。"""
    bucket = {}
    for r in rows:
        dept = r["dept_name"]
        item = bucket.setdefault(dept, {
            "dept_name": dept,
            "device_amount": 0,
            "invest_amount": 0.0,
            "net_profit_year": 0.0,
            "roi_percent": 0.0,
        })
        item["device_amount"] += 1
        item["invest_amount"] += r["invest_amount"]
        item["net_profit_year"] += r["net_profit_year"]
    for item in bucket.values():
        item["invest_amount"] = round(item["invest_amount"], 2)
        item["net_profit_year"] = round(item["net_profit_year"], 2)
        item["roi_percent"] = (round(item["net_profit_year"]
                                     / item["invest_amount"] * 100, 2)
                               if item["invest_amount"] > 0 else 0.0)
    return sorted(bucket.values(), key=lambda x: x["roi_percent"], reverse=True)


def build_roi_chart_data(rows):
    """组织 ROI 分析图表数据：排行榜、盈亏结构、科室对比、散点分布。"""
    rank_rows = sorted(rows, key=lambda x: x["roi_percent"], reverse=True)[:10]
    profit_structure = [
        {"name": "盈利设备", "value": sum(1 for r in rows if r["profit_flag"] == 1)},
        {"name": "亏损设备", "value": sum(1 for r in rows if r["profit_flag"] == 0)},
    ]
    scatter_rows = [
        {"asset_name": r["asset_name"], "used_years": r["used_years"],
         "roi_percent": r["roi_percent"], "invest_amount": r["invest_amount"]}
        for r in rows
    ]
    return {
        "rank": rank_rows,
        "profit_structure": profit_structure,
        "dept_summary": build_dept_roi_summary(rows),
        "scatter": scatter_rows,
    }


def filter_roi_rows(rows, keyword="", dept_name="", level_code="", profit_flag=""):
    """按关键字、科室、回报等级、盈亏标志过滤测算明细。"""
    result = rows
    if keyword:
        kw = keyword.strip().lower()
        result = [r for r in result
                  if kw in str(r["asset_name"]).lower()
                  or kw in str(r["asset_code"]).lower()]
    if dept_name:
        result = [r for r in result if r["dept_name"] == dept_name]
    if level_code:
        result = [r for r in result if r["level_code"] == level_code]
    if profit_flag in ("1", "0"):
        result = [r for r in result if r["profit_flag"] == int(profit_flag)]
    return result


def build_roi_payload(keyword="", dept_name="", level_code="", profit_flag=""):
    """组装 ROI 分析页完整数据：汇总、图表、明细、等级与科室选项。"""
    all_rows = build_roi_rows()
    shown = filter_roi_rows(all_rows, keyword, dept_name, level_code, profit_flag)
    return {
        "summary": summarize_roi(all_rows),
        "charts": build_roi_chart_data(all_rows),
        "rows": sorted(shown, key=lambda x: x["roi_percent"], reverse=True),
        "row_amount": len(shown),
        "level_options": [{"code": k, "label": v}
                          for k, v in ROI_LEVEL_DICT.items()],
        "dept_options": sorted({r["dept_name"] for r in all_rows}),
    }


def fetch_roi_profile(asset_code):
    """查询单台设备的年度收支档案。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT asset_code, annual_income, annual_maintain_cost, "
            "annual_other_cost, stat_year FROM md_roi_profile WHERE asset_code=?",
            (asset_code,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def save_roi_profile(asset_code, annual_income, annual_maintain_cost,
                     annual_other_cost, stat_year=""):
    """写入或更新设备年度收支档案，并同步归档本次测算结果。"""
    if not asset_code:
        return {"result": "FAIL", "message": "资产编号不能为空"}
    income = float(annual_income or 0)
    maintain = float(annual_maintain_cost or 0)
    other = float(annual_other_cost or 0)
    if income < 0 or maintain < 0 or other < 0:
        return {"result": "FAIL", "message": "年度收支金额不能为负数"}

    conn = get_connection()
    try:
        asset = conn.execute(
            "SELECT asset_id, asset_code, asset_name, asset_category, dept_name, "
            "purchase_date, purchase_price, service_life, current_status "
            "FROM md_asset_ledger WHERE asset_code=?", (asset_code,)
        ).fetchone()
        if asset is None:
            return {"result": "FAIL", "message": "资产编号不存在：%s" % asset_code}

        old = conn.execute(
            "SELECT profile_id FROM md_roi_profile WHERE asset_code=?",
            (asset_code,)
        ).fetchone()
        if old is None:
            conn.execute(
                "INSERT INTO md_roi_profile (asset_code, annual_income, "
                "annual_maintain_cost, annual_other_cost, stat_year) "
                "VALUES (?,?,?,?,?)",
                (asset_code, income, maintain, other,
                 stat_year or str(datetime.date.today().year)),
            )
        else:
            conn.execute(
                "UPDATE md_roi_profile SET annual_income=?, annual_maintain_cost=?, "
                "annual_other_cost=?, stat_year=?, "
                "updated_at=datetime('now','localtime') WHERE asset_code=?",
                (income, maintain, other,
                 stat_year or str(datetime.date.today().year), asset_code),
            )
        conn.commit()
    finally:
        conn.close()

    write_ops_journal("ROI测算", "修改", asset_code,
                      "更新年度收支：创收%.2f 维保%.2f 其他%.2f"
                      % (income, maintain, other))

    asset_row = dict(asset)
    result = evaluate_single_roi(asset_row, fetch_roi_profile(asset_code))
    archive_roi_snapshot(result)
    return {"result": "OK", "message": "年度收支档案已保存", "detail": result}


def archive_roi_snapshot(result):
    """把单台设备的测算结果写入归档表，支撑历史回报率追溯。"""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO md_roi_snapshot (asset_code, asset_name, dept_name, "
            "invest_amount, net_profit_year, roi_percent, payback_years, "
            "profit_flag, level_code) VALUES (?,?,?,?,?,?,?,?,?)",
            (result["asset_code"], result["asset_name"], result["dept_name"],
             result["invest_amount"], result["net_profit_year"],
             result["roi_percent"], result["payback_years"],
             result["profit_flag"], result["level_code"]),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def fetch_roi_history(asset_code, limit=12):
    """查询单台设备的历史测算归档，按时间倒序返回。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT calc_time, roi_percent, net_profit_year, level_code "
            "FROM md_roi_snapshot WHERE asset_code=? "
            "ORDER BY snapshot_id DESC LIMIT ?", (asset_code, limit)
        ).fetchall()
    finally:
        conn.close()
    history = [dict(r) for r in rows]
    for item in history:
        item["level_name"] = ROI_LEVEL_DICT.get(item["level_code"], "未分级")
    return list(reversed(history))


def seed_demo_roi_profiles():
    """灌入演示用年度收支档案，保证第 4 天启动即可看到 ROI 分析效果。

    收入水平按设备分类差异化设置，使演示数据同时出现盈利与亏损设备。
    """
    conn = get_connection()
    try:
        exist = conn.execute(
            "SELECT COUNT(*) AS c FROM md_roi_profile").fetchone()["c"]
        if exist > 0:
            return 0
        assets = conn.execute(
            "SELECT asset_code, asset_category, purchase_price "
            "FROM md_asset_ledger ORDER BY asset_id"
        ).fetchall()
        # 分类收入系数：影像类创收高、辅助类创收低，制造盈亏差异
        income_factor = {
            "IMAGE": 0.42, "LAB": 0.36, "TREAT": 0.30,
            "MONITOR": 0.18, "SURGERY": 0.26, "AUX": 0.10,
        }
        demo_rows = []
        for idx, row in enumerate(assets):
            price = float(row["purchase_price"] or 0)
            factor = income_factor.get(row["asset_category"], 0.15)
            # 每隔几台压低收入，构造亏损样本
            if idx % 4 == 3:
                factor = factor * 0.35
            income = round(price * factor, 2)
            maintain = round(price * (0.05 if idx % 3 else 0.02), 2)
            other = round(price * 0.02, 2)
            demo_rows.append((row["asset_code"], income, maintain, other,
                              str(datetime.date.today().year)))
        conn.executemany(
            "INSERT OR IGNORE INTO md_roi_profile (asset_code, annual_income, "
            "annual_maintain_cost, annual_other_cost, stat_year) "
            "VALUES (?,?,?,?,?)", demo_rows)
        conn.commit()
        amount = len(demo_rows)
    finally:
        conn.close()
    if amount:
        write_ops_journal("ROI测算", "初始化", "DEMO-ROI",
                          "灌入演示年度收支档案 %d 条" % amount)
    return amount


def build_roi_brief():
    """为运维总览看板提供 ROI 概览小卡片数据。"""
    rows = build_roi_rows()
    summary = summarize_roi(rows)
    return {
        "profit_amount": summary["profit_amount"],
        "loss_amount": summary["loss_amount"],
        "avg_roi_percent": summary["avg_roi_percent"],
        "total_net_year": summary["total_net_year"],
    }

# --------------------------------------------------------------------------
# 第 4 天数据表脚本增量：mdm_schema.sql（M，已内联为字符串常量 DAY4_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY4_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 4 天新增：设备年度收支档案表（ROI 计算基础数据）
-- ==================================================================
-- 设备年度收支档案表：登记每台设备的年度创收与年度支出，作为 ROI
-- 投入产出回报测算的分子来源；与台账表按资产编号一对一关联
CREATE TABLE IF NOT EXISTS md_roi_profile (
    profile_id           INTEGER PRIMARY KEY AUTOINCREMENT,  -- 档案主键
    asset_code           TEXT    NOT NULL UNIQUE,            -- 关联设备资产编号
    annual_income        REAL    DEFAULT 0,                  -- 年度创收金额（元）
    annual_maintain_cost REAL    DEFAULT 0,                  -- 年度维保支出（元）
    annual_other_cost    REAL    DEFAULT 0,                  -- 年度其他支出（耗材/人工等）
    stat_year            TEXT    DEFAULT '',                 -- 数据所属统计年度
    updated_at           TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 4 天新增：ROI 测算结果归档表
-- ==================================================================
-- ROI 测算归档表：每次测算落一份快照，支撑历史回报率变化趋势追溯
CREATE TABLE IF NOT EXISTS md_roi_snapshot (
    snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,   -- 快照主键
    asset_code      TEXT    NOT NULL,                    -- 设备资产编号
    asset_name      TEXT    DEFAULT '',                  -- 设备名称
    dept_name       TEXT    DEFAULT '',                  -- 使用科室
    invest_amount   REAL    DEFAULT 0,                   -- 投资总额（采购原值）
    net_profit_year REAL    DEFAULT 0,                   -- 年净收益
    roi_percent     REAL    DEFAULT 0,                   -- 投资回报率（%）
    payback_years   REAL    DEFAULT 0,                   -- 预计回本年限
    profit_flag     INTEGER DEFAULT 0,                   -- 盈亏标志：1 盈利 / 0 亏损
    level_code      TEXT    DEFAULT '',                  -- 回报等级编码
    calc_time       TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_roi_asset  ON md_roi_profile (asset_code);
CREATE INDEX IF NOT EXISTS idx_roisnap_code ON md_roi_snapshot (asset_code);
CREATE INDEX IF NOT EXISTS idx_roisnap_time ON md_roi_snapshot (calc_time);
"""


def apply_day4_schema(conn):
    """把第 4 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY4_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 4 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-24（第 4 天 / 共 15 天）
#           注册运维总览看板、设备台账 CRUD、设备状态管理与 ROI 投入产出
#           分析四套页面路由，并提供台账增删改查、闲置识别扫描、状态切换、
#           ROI 测算与年度收支档案维护等 RESTful 接口，供前端表格、弹窗与
#           ECharts 图表异步取数

from mdm_roi import (
    build_roi_payload,
    build_roi_brief,
    fetch_roi_profile,
    save_roi_profile,
    fetch_roi_history,
    archive_roi_snapshot,
    seed_demo_roi_profiles,
    ROI_LEVEL_DICT,
)

# 【函数内插入】def api_overview():
#    payload["roi_brief"] = build_roi_brief()

# ==================================================================
# 以下为第 4 天新增：设备 ROI 投入产出回报计算接口
# ==================================================================


@app.route("/md/roi")
def panel_roi():
    """ROI 投入产出分析页面：回报概览、排行榜、盈亏结构与明细维护。"""
    write_ops_journal("ROI测算", "查询", "PANEL-ROI", "打开 ROI 投入产出分析页面")
    return render_template(
        "panel_roi.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/md/roi/stat")
def api_roi_statistics():
    """ROI 分析数据接口：汇总指标、图表数据与明细列表。"""
    payload = build_roi_payload(
        keyword=(request.args.get("keyword") or "").strip(),
        dept_name=(request.args.get("dept_name") or "").strip(),
        level_code=(request.args.get("level_code") or "").strip(),
        profit_flag=(request.args.get("profit_flag") or "").strip(),
    )
    payload["result"] = "OK"
    return jsonify(payload)


@app.route("/api/md/roi/profile", methods=["GET", "POST"])
def api_roi_profile():
    """年度收支档案读写接口：GET 查单台档案，POST 保存并重算 ROI。"""
    if request.method == "GET":
        asset_code = (request.args.get("asset_code") or "").strip()
        profile = fetch_roi_profile(asset_code)
        return jsonify({
            "result": "OK" if profile else "FAIL",
            "profile": profile,
            "history": fetch_roi_history(asset_code) if asset_code else [],
        })
    payload = request.get_json(silent=True) or {}
    outcome = save_roi_profile(
        (payload.get("asset_code") or "").strip(),
        payload.get("annual_income") or 0,
        payload.get("annual_maintain_cost") or 0,
        payload.get("annual_other_cost") or 0,
        (payload.get("stat_year") or "").strip(),
    )
    return jsonify(outcome)


@app.route("/api/md/roi/archive", methods=["POST"])
def api_roi_archive_all():
    """批量归档接口：把当前全部设备的测算结果写入 ROI 归档表。"""
    payload = build_roi_payload()
    for row in payload["rows"]:
        archive_roi_snapshot(row)
    return jsonify({
        "result": "OK",
        "message": "ROI 测算结果已归档 %d 台设备" % payload["row_amount"],
    })


@app.route("/api/md/roi/options")
def api_roi_options():
    """ROI 页面下拉选项接口：回报等级字典与科室列表。"""
    payload = build_roi_payload()
    return jsonify({
        "result": "OK",
        "level_options": payload["level_options"],
        "dept_options": payload["dept_options"],
        "level_legend": [{"code": k, "label": v}
                         for k, v in ROI_LEVEL_DICT.items()],
    })



# 【函数内插入】def bootstrap_application():
#    seed_demo_roi_profiles()

# 【函数内插入】if __name__ == "__main__":
#    print("ROI 分析： http://127.0.0.1:%d/md/roi" % PORT)

# --------------------------------------------------------------------------
# 第 4 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/mdm_roi.js                新增 264 行
#   [修改] static/mdm_theme.css             新增 47 行
#   [修改] templates/layout_mdm.html        新增 1 行
#   [新增] templates/panel_roi.html         新增 164 行
