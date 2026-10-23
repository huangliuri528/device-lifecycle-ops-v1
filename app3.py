# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-23（第 3 天 / 共 15 天）
# 模块功能：本文件为该软件的完整源代码单文件版，按依赖顺序依次内联配置模块、建表脚本、数据仓储层、各业务模块与 Flask 主程序入口，可独立阅读与归档，用于计算机软件著作权申报
# 依赖包：Flask==3.0.3
# 启动命令：python 医疗器械全生命周期运维管理平台V1.0.py （默认 http://127.0.0.1:5001/md/overview）
# 说明：运行需与本工程 templates/、static/ 目录同级放置

import os
import sqlite3
import datetime
from flask import Flask, render_template, jsonify, redirect, url_for, request

# --------------------------------------------------------------------------
# 模块 1：mdm_settings.py
# 模块功能：全局运行参数配置中心，集中管理平台端口、数据库文件路径、分页尺寸、 设备状态字典编码等常量，供各业务模块统一引用，避免硬编码散落
# --------------------------------------------------------------------------


# ------------------------------------------------------------------
# 作者信息预留位（软著申报时可按需修改，不会影响程序逻辑）
# ------------------------------------------------------------------
AUTHOR_NAME = "【作者姓名预留】"
AUTHOR_ORG = "【作者单位预留】"
SOFTWARE_FULL_NAME = "医疗器械全生命周期运维管理平台"
SOFTWARE_VERSION = "V1.0"
SOFTWARE_CODE = "MDM-LIFECYCLE-OPS"

# ------------------------------------------------------------------
# 路径与运行参数
# ------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "mdm_lifecycle.db")
SCHEMA_FILE = os.path.join(BASE_DIR, "mdm_schema.sql")

HOST = "0.0.0.0"
PORT = 5001
DEBUG = True
PAGE_SIZE = 15

# ------------------------------------------------------------------
# 设备状态字典（台账模块与看板统计共用同一套编码，保证统计口径一致）
# ------------------------------------------------------------------
ASSET_STATUS_DICT = {
    "IN_USE": "在用",
    "IDLE": "闲置",
    "REPAIR": "维修中",
    "SCRAP": "已报废",
    "STOCK": "库存待用",
}

# 风险等级字典（用于维保到期与闲置预警的等级判定）
RISK_LEVEL_DICT = {
    0: "正常",
    1: "关注",
    2: "预警",
    3: "严重",
}

# 设备分类字典（台账分类统计使用）
ASSET_CATEGORY_DICT = {
    "DIAG": "影像诊断类",
    "LAB": "检验分析类",
    "TREAT": "治疗救护类",
    "MONITOR": "监护类",
    "AUX": "辅助支持类",
}


def ensure_data_dir():
    """创建数据存放目录，保证 SQLite 文件写入位置存在。"""
    if not os.path.isdir(DATA_DIR):
        os.makedirs(DATA_DIR)
    return DATA_DIR


# --------------------------------------------------------------------------
# 内联建表脚本：mdm_schema.sql（原 .sql 文件，此处转为字符串常量 MDM_SCHEMA_SQL）
# --------------------------------------------------------------------------
MDM_SCHEMA_SQL = r"""
-- ==================================================================
-- 软件全称：医疗器械全生命周期运维管理平台 V1.0
-- 版本：V1.0
-- 开发日期：2026-10-21（第 1 天 / 共 15 天）
-- 模块功能：系统初始化建表脚本，定义设备台账主表、状态流转记录表、
--           运维操作日志表三张基础数据结构，为后续 14 天的业务模块
--           迭代提供统一的数据底座
-- 依赖包：SQLite3（Python 内置）
-- 使用方式：由 mdm_dal.py 在首次启动时自动执行，也可手动 sqlite3 导入
-- ==================================================================

PRAGMA foreign_keys = ON;

-- 设备台账主表：记录医疗机构每一台医疗器械的基本档案与当前状态
CREATE TABLE IF NOT EXISTS md_asset_ledger (
    asset_id        INTEGER PRIMARY KEY AUTOINCREMENT,  -- 台账主键
    asset_code      TEXT    NOT NULL UNIQUE,            -- 设备资产编号（院内唯一码）
    asset_name      TEXT    NOT NULL,                   -- 设备名称
    asset_model     TEXT    DEFAULT '',                 -- 规格型号
    asset_category  TEXT    DEFAULT 'AUX',              -- 设备分类编码（见 ASSET_CATEGORY_DICT）
    vendor_name     TEXT    DEFAULT '',                 -- 生产厂家
    dept_name       TEXT    DEFAULT '',                 -- 使用科室
    location_text   TEXT    DEFAULT '',                 -- 存放位置
    purchase_date   TEXT    DEFAULT '',                 -- 采购日期（YYYY-MM-DD）
    purchase_price  REAL    DEFAULT 0,                  -- 采购原值（元）
    service_life    INTEGER DEFAULT 0,                  -- 预计使用年限（年）
    current_status  TEXT    DEFAULT 'STOCK',            -- 当前状态编码（见 ASSET_STATUS_DICT）
    maintain_cycle  INTEGER DEFAULT 0,                  -- 维保周期（天）
    last_maintain_at TEXT   DEFAULT '',                 -- 最近一次维保日期
    next_maintain_at TEXT   DEFAULT '',                 -- 下一次维保到期日期
    custodian       TEXT    DEFAULT '',                 -- 责任人/保管人
    remark_text     TEXT    DEFAULT '',                 -- 备注
    created_at      TEXT    DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 设备状态流转记录表：登记每一次状态变更，支撑全生命周期追溯
CREATE TABLE IF NOT EXISTS md_status_flow (
    flow_id       INTEGER PRIMARY KEY AUTOINCREMENT,   -- 流转记录主键
    asset_id      INTEGER NOT NULL,                    -- 关联设备台账
    from_status   TEXT    DEFAULT '',                  -- 变更前状态
    to_status     TEXT    NOT NULL,                    -- 变更后状态
    change_reason TEXT    DEFAULT '',                  -- 变更原因说明
    operator_name TEXT    DEFAULT '',                  -- 操作人
    flow_time     TEXT    DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (asset_id) REFERENCES md_asset_ledger (asset_id) ON DELETE CASCADE
);

-- 运维操作日志表：记录管理员在平台内的关键操作，用于审计追溯
CREATE TABLE IF NOT EXISTS md_ops_journal (
    journal_id    INTEGER PRIMARY KEY AUTOINCREMENT,   -- 日志主键
    module_name   TEXT    DEFAULT '',                  -- 操作所属模块
    action_type   TEXT    DEFAULT '',                  -- 操作类型（新增/修改/删除/查询/导出）
    target_code   TEXT    DEFAULT '',                  -- 操作对象编号
    action_detail TEXT    DEFAULT '',                  -- 操作详情描述
    operator_name TEXT    DEFAULT 'system',            -- 操作人
    action_time   TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 建索引：台账表按状态与科室高频检索，建立组合索引提升查询效率
CREATE INDEX IF NOT EXISTS idx_ledger_status ON md_asset_ledger (current_status);
CREATE INDEX IF NOT EXISTS idx_ledger_dept   ON md_asset_ledger (dept_name);
CREATE INDEX IF NOT EXISTS idx_flow_asset    ON md_status_flow (asset_id);

-- ==================================================================
-- 第 3 天新增：闲置设备判定规则表
-- ==================================================================
-- 闲置判定规则表：按设备分类配置「连续闲置达多少天即判定为长期闲置」
-- 与风险等级上限，供闲置识别模块扫描时逐类套用，规则可由管理员维护
CREATE TABLE IF NOT EXISTS md_idle_rule (
    rule_id         INTEGER PRIMARY KEY AUTOINCREMENT,  -- 规则主键
    category_code   TEXT    NOT NULL UNIQUE,            -- 设备分类编码
    threshold_days  INTEGER DEFAULT 90,                 -- 长期闲置判定阈值（天）
    risk_level      INTEGER DEFAULT 2,                  -- 超出阈值后的风险等级（见 RISK_LEVEL_DICT）
    rule_desc       TEXT    DEFAULT '',                 -- 规则说明
    enabled_flag    INTEGER DEFAULT 1,                  -- 是否启用：1 启用 / 0 停用
    updated_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- 闲置识别结果表：保存每一次扫描得到的闲置设备快照，支撑闲置趋势回溯
CREATE TABLE IF NOT EXISTS md_idle_snapshot (
    snap_id        INTEGER PRIMARY KEY AUTOINCREMENT,   -- 快照主键
    asset_id       INTEGER NOT NULL,                    -- 关联设备台账
    asset_code     TEXT    DEFAULT '',                  -- 资产编号快照
    idle_days      INTEGER DEFAULT 0,                   -- 识别到的连续闲置天数
    threshold_days INTEGER DEFAULT 0,                   -- 当时套用的阈值
    risk_level     INTEGER DEFAULT 0,                   -- 判定风险等级
    idle_value     REAL    DEFAULT 0,                   -- 闲置资产原值（元）
    scan_date      TEXT    DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (asset_id) REFERENCES md_asset_ledger (asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_snap_asset ON md_idle_snapshot (asset_id);
CREATE INDEX IF NOT EXISTS idx_snap_date  ON md_idle_snapshot (scan_date);
"""


# --------------------------------------------------------------------------
# 模块 3：mdm_dal.py
# 模块功能：数据访问层（DAL）。负责 SQLite 连接管理、建表脚本执行、 字典翻译、台账统计汇总，以及运维日志写入。所有业务模块 统一通过本文件访问数据库，禁止在路由中直接拼接 SQL
# --------------------------------------------------------------------------



def get_connection():
    """获取数据库连接，统一设置 row_factory 使查询结果可按列名访问。"""
    ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_database():
    """执行建表脚本，创建第 1 天所需的三张基础表。"""
    ensure_data_dir()
    # 单文件版：建表脚本已内联为 MDM_SCHEMA_SQL 常量，无需外部 .sql 文件
    script = MDM_SCHEMA_SQL
    conn = get_connection()
    try:
        conn.executescript(script)
        conn.commit()
    finally:
        conn.close()
    return True


def translate_status(status_code):
    """把设备状态编码翻译为中文名称，未识别编码返回原值。"""
    return ASSET_STATUS_DICT.get(status_code, status_code)


def translate_category(category_code):
    """把设备分类编码翻译为中文名称。"""
    return ASSET_CATEGORY_DICT.get(category_code, category_code)


def rows_to_dict_list(cursor_rows):
    """把 sqlite3.Row 结果集转换为标准字典列表，便于 jsonify 输出。"""
    return [dict(row) for row in cursor_rows]


def count_asset_total():
    """统计台账设备总数量。"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(1) AS cnt FROM md_asset_ledger").fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def summarize_asset_by_status():
    """按设备状态分组统计数量，用于看板状态饼图。

    返回示例：[{"status_code": "IN_USE", "status_label": "在用", "amount": 12}, ...]
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT current_status AS status_code, COUNT(1) AS amount "
            "FROM md_asset_ledger GROUP BY current_status ORDER BY amount DESC"
        ).fetchall()
    finally:
        conn.close()
    result = []
    for item in rows_to_dict_list(rows):
        item["status_label"] = translate_status(item["status_code"])
        result.append(item)
    return result


def summarize_asset_by_category():
    """按设备分类分组统计数量，用于看板分类柱状图。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT asset_category AS category_code, COUNT(1) AS amount "
            "FROM md_asset_ledger GROUP BY asset_category"
        ).fetchall()
    finally:
        conn.close()
    result = []
    for item in rows_to_dict_list(rows):
        item["category_label"] = translate_category(item["category_code"])
        result.append(item)
    return result


def fetch_asset_brief_list(limit=20):
    """读取最近登记的台账摘要列表，用于看板底部的设备清单卡片。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT asset_id, asset_code, asset_name, dept_name, current_status, "
            "next_maintain_at FROM md_asset_ledger "
            "ORDER BY asset_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for item in rows_to_dict_list(rows):
        item["status_label"] = translate_status(item["current_status"])
        result.append(item)
    return result


def build_overview_payload():
    """组装运维总览看板所需的全部统计数据。

    第 1 天版本输出：设备总数、状态分布、分类分布、近期台账清单。
    后续第 3 天（闲置识别）、第 4 天（ROI）、第 5 天（维保到期）
    将在本函数基础上叠加新的统计字段。
    """
    status_rows = summarize_asset_by_status()
    status_map = {item["status_code"]: item["amount"] for item in status_rows}
    return {
        "asset_total": count_asset_total(),
        "status_distribution": status_rows,
        "category_distribution": summarize_asset_by_category(),
        "status_amount": {
            "in_use": status_map.get("IN_USE", 0),
            "idle": status_map.get("IDLE", 0),
            "repair": status_map.get("REPAIR", 0),
            "scrap": status_map.get("SCRAP", 0),
            "stock": status_map.get("STOCK", 0),
        },
        "recent_assets": fetch_asset_brief_list(20),
    }


def write_ops_journal(module_name, action_type, target_code, action_detail, operator="system"):
    """写入一条运维操作日志，供第 6 天的日志查询页面检索。"""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO md_ops_journal "
            "(module_name, action_type, target_code, action_detail, operator_name) "
            "VALUES (?, ?, ?, ?, ?)",
            (module_name, action_type, target_code, action_detail, operator),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def seed_demo_assets():
    """写入演示用台账样例数据，便于首日启动即可看到看板效果。

    若台账已有数据则跳过，避免重复灌数。
    """
    if count_asset_total() > 0:
        return 0
    demo_rows = [
        ("MDM-2026-001", "全自动生化分析仪", "BS-2000", "LAB", "迈瑞", "检验科", "门诊楼3层", "2024-03-12", 386000, 8, "IN_USE", 180, "2026-04-10", "2026-10-07", "张伟"),
        ("MDM-2026-002", "数字化X射线摄影系统", "DR-800", "DIAG", "万东", "放射科", "医技楼1层", "2023-09-01", 1250000, 10, "IN_USE", 180, "2026-05-20", "2026-11-16", "李娜"),
        ("MDM-2026-003", "多参数监护仪", "PM-9000", "MONITOR", "理邦", "急诊科", "急诊楼2层", "2022-06-18", 48000, 6, "IDLE", 365, "2025-11-02", "2026-11-02", "王强"),
        ("MDM-2026-004", "便携式彩色超声仪", "US-300", "DIAG", "开立", "超声科", "门诊楼4层", "2025-01-20", 268000, 8, "IN_USE", 180, "2026-06-15", "2026-12-12", "陈静"),
        ("MDM-2026-005", "高频电刀", "ES-120", "TREAT", "沪通", "手术室", "住院楼5层", "2021-11-05", 76000, 8, "REPAIR", 180, "2026-02-08", "2026-08-07", "刘洋"),
        ("MDM-2026-006", "医用离心机", "CT-60", "LAB", "湘仪", "检验科", "门诊楼3层", "2020-07-22", 32000, 10, "SCRAP", 365, "2025-07-10", "2026-07-10", "张伟"),
        ("MDM-2026-007", "输液泵", "IP-50", "AUX", "深圳科曼", "内科病房", "住院楼3层", "2024-10-09", 9800, 5, "STOCK", 365, "", "", "赵敏"),
        ("MDM-2026-008", "电子胃镜系统", "EG-500", "TREAT", "奥林巴斯", "内镜中心", "门诊楼2层", "2023-04-16", 890000, 10, "IN_USE", 180, "2026-03-05", "2026-09-01", "孙磊"),
    ]
    conn = get_connection()
    try:
        conn.executemany(
            "INSERT INTO md_asset_ledger "
            "(asset_code, asset_name, asset_model, asset_category, vendor_name, dept_name, "
            "location_text, purchase_date, purchase_price, service_life, current_status, "
            "maintain_cycle, last_maintain_at, next_maintain_at, custodian) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            demo_rows,
        )
        conn.commit()
        # 同步登记建档流转记录：演示数据需带有状态起点时间，
        # 供第 3 天闲置识别模块计算连续闲置天数（闲置设备给较早的起点）
        for row in demo_rows:
            asset_code, status = row[0], row[10]
            offset = -128 if status == "IDLE" else (-12 if status == "REPAIR" else -30)
            conn.execute(
                "INSERT INTO md_status_flow (asset_id, from_status, to_status, change_reason, "
                "operator_name, flow_time) VALUES "
                "((SELECT asset_id FROM md_asset_ledger WHERE asset_code=?), '', ?, '台账建档', "
                "'system', datetime('now','localtime','%d days'))" % offset,
                (asset_code, status),
            )
        conn.commit()
        write_ops_journal("台账初始化", "新增", "DEMO-SEED", "导入演示台账样例数据 %d 条" % len(demo_rows))
    finally:
        conn.close()
    return len(demo_rows)


# ==================================================================
# 以下为第 2 天新增：设备台账增删改查底层 SQL 支撑
# ==================================================================

ASSET_EDITABLE_FIELDS = [
    "asset_name", "asset_model", "asset_category", "vendor_name", "dept_name",
    "location_text", "purchase_date", "purchase_price", "service_life",
    "current_status", "maintain_cycle", "last_maintain_at", "next_maintain_at",
    "custodian", "remark_text",
]


def fetch_asset_by_id(asset_id):
    """按主键读取单条台账记录。"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM md_asset_ledger WHERE asset_id=?", (asset_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def fetch_asset_by_code(asset_code):
    """按资产编号读取台账记录，用于编号重复性校验。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT asset_id FROM md_asset_ledger WHERE asset_code=?", (asset_code,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_asset_row(asset_code, field_values):
    """写入一条新的台账记录，返回新记录主键。"""
    columns = ["asset_code"] + ASSET_EDITABLE_FIELDS
    holders = ",".join(["?"] * len(columns))
    sql = "INSERT INTO md_asset_ledger (%s) VALUES (%s)" % (",".join(columns), holders)
    params = [asset_code] + [field_values.get(name, "") for name in ASSET_EDITABLE_FIELDS]
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_asset_row(asset_id, field_values):
    """按主键更新台账字段，只更新传入的字段。"""
    if not field_values:
        return False
    columns = [name for name in field_values.keys() if name in ASSET_EDITABLE_FIELDS]
    if not columns:
        return False
    setter = ",".join(["%s=?" % name for name in columns])
    setter += ", updated_at=datetime('now','localtime')"
    params = [field_values[name] for name in columns] + [asset_id]
    conn = get_connection()
    try:
        conn.execute("UPDATE md_asset_ledger SET %s WHERE asset_id=?" % setter, params)
        conn.commit()
    finally:
        conn.close()
    return True


def delete_asset_row(asset_id):
    """删除一条台账记录（关联的状态流转记录由外键级联删除）。"""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM md_asset_ledger WHERE asset_id=?", (asset_id,))
        conn.commit()
    finally:
        conn.close()
    return True


def query_asset_paged(keyword="", status_code="", category_code="", page=1, size=15):
    """多条件分页查询台账。

    keyword 同时匹配资产编号、设备名称、责任人；status_code / category_code 精确匹配。
    返回 (记录列表, 总条数, 总页数)。
    """
    where_parts = []
    params = []
    if keyword:
        where_parts.append("(asset_code LIKE ? OR asset_name LIKE ? OR custodian LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like, like])
    if status_code:
        where_parts.append("current_status=?")
        params.append(status_code)
    if category_code:
        where_parts.append("asset_category=?")
        params.append(category_code)
    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(1) AS cnt FROM md_asset_ledger" + where_sql, params
        ).fetchone()["cnt"]
        total_page = max(1, (total + size - 1) // size)
        page = min(max(1, page), total_page)
        offset = (page - 1) * size
        rows = conn.execute(
            "SELECT asset_id, asset_code, asset_name, asset_model, asset_category, vendor_name, "
            "dept_name, location_text, purchase_date, purchase_price, service_life, current_status, "
            "maintain_cycle, next_maintain_at, custodian, remark_text "
            "FROM md_asset_ledger" + where_sql +
            " ORDER BY asset_id DESC LIMIT ? OFFSET ?",
            params + [size, offset],
        ).fetchall()
    finally:
        conn.close()
    result = []
    for item in rows_to_dict_list(rows):
        item["status_label"] = translate_status(item["current_status"])
        item["category_label"] = translate_category(item["asset_category"])
        result.append(item)
    return result, total, total_page


def append_status_flow(asset_id, from_status, to_status, reason, operator="system"):
    """登记一条状态流转记录，支撑全生命周期追溯。"""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO md_status_flow (asset_id, from_status, to_status, change_reason, operator_name) "
            "VALUES (?,?,?,?,?)",
            (asset_id, from_status, to_status, reason, operator),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def fetch_status_flow(asset_id):
    """读取某台设备的状态流转历史。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT from_status, to_status, change_reason, operator_name, flow_time "
            "FROM md_status_flow WHERE asset_id=? ORDER BY flow_id DESC LIMIT 20",
            (asset_id,),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for item in rows_to_dict_list(rows):
        item["from_label"] = translate_status(item["from_status"])
        item["to_label"] = translate_status(item["to_status"])
        result.append(item)
    return result


def fetch_latest_journals(limit=15):
    """读取最近的运维操作日志，供页面侧栏展示。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT module_name, action_type, target_code, action_detail, operator_name, action_time "
            "FROM md_ops_journal ORDER BY journal_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return rows_to_dict_list(rows)


# --------------------------------------------------------------------------
# 模块 4：mdm_ledger.py
# 模块功能：设备台账业务层。封装台账新增、修改、删除、分页检索的标准流程， 包含资产编号自动生成、字段合法性校验、状态变更自动登记流转记录 与运维日志留痕，供 Flask 路由层直接调用
# --------------------------------------------------------------------------



def today_text():
    """返回今天日期字符串。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def generate_asset_code():
    """自动生成资产编号：MDM-年份-三位流水号。

    规则示例：MDM-2026-009（在现有最大流水号基础上递增）。
    """
    year = datetime.date.today().year
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT asset_code FROM md_asset_ledger WHERE asset_code LIKE ? "
            "ORDER BY asset_id DESC LIMIT 1",
            ("MDM-%d-%%" % year,),
        ).fetchone()
    finally:
        conn.close()
    seq = 1
    if row:
        tail = row["asset_code"].split("-")[-1]
        if tail.isdigit():
            seq = int(tail) + 1
    return "MDM-%d-%03d" % (year, seq)


def validate_asset_payload(payload, for_update=False):
    """校验台账表单数据。

    返回 (是否通过, 错误提示, 规范化后的字段字典)。
    校验项：设备名称必填、分类与状态编码合法、金额与年限为非负数。
    """
    errors = []
    name = (payload.get("asset_name") or "").strip()
    if not name:
        errors.append("设备名称不能为空")

    category = (payload.get("asset_category") or "AUX").strip()
    if category not in ASSET_CATEGORY_DICT:
        errors.append("设备分类编码不合法：%s" % category)

    status = (payload.get("current_status") or "STOCK").strip()
    if status not in ASSET_STATUS_DICT:
        errors.append("设备状态编码不合法：%s" % status)

    def to_number(text, default=0.0):
        try:
            value = float(text)
            return value if value >= 0 else default
        except (TypeError, ValueError):
            return default

    price = to_number(payload.get("purchase_price"), 0.0)
    if price < 0:
        errors.append("采购原值不能为负数")

    fields = {
        "asset_name": name,
        "asset_model": (payload.get("asset_model") or "").strip(),
        "asset_category": category,
        "vendor_name": (payload.get("vendor_name") or "").strip(),
        "dept_name": (payload.get("dept_name") or "").strip(),
        "location_text": (payload.get("location_text") or "").strip(),
        "purchase_date": (payload.get("purchase_date") or "").strip(),
        "purchase_price": price,
        "service_life": int(to_number(payload.get("service_life"), 0)),
        "current_status": status,
        "maintain_cycle": int(to_number(payload.get("maintain_cycle"), 0)),
        "last_maintain_at": (payload.get("last_maintain_at") or "").strip(),
        "next_maintain_at": (payload.get("next_maintain_at") or "").strip(),
        "custodian": (payload.get("custodian") or "").strip(),
        "remark_text": (payload.get("remark_text") or "").strip(),
    }
    return (len(errors) == 0), ("；".join(errors) if errors else ""), fields


def create_asset_record(payload, operator="admin"):
    """新增一条台账记录，并登记状态流转与操作日志。"""
    ok, message, fields = validate_asset_payload(payload)
    if not ok:
        return {"success": False, "message": message}

    asset_code = (payload.get("asset_code") or "").strip()
    if not asset_code:
        asset_code = generate_asset_code()
    elif fetch_asset_by_code(asset_code):
        return {"success": False, "message": "资产编号 %s 已存在" % asset_code}

    asset_id = insert_asset_row(asset_code, fields)
    append_status_flow(asset_id, "", fields["current_status"], "台账建档", operator)
    write_ops_journal(
        "设备台账", "新增", asset_code,
        "新增设备：%s（%s）" % (fields["asset_name"], fields["dept_name"]), operator,
    )
    return {"success": True, "asset_id": asset_id, "asset_code": asset_code,
            "message": "台账新增成功"}


def modify_asset_record(asset_id, payload, operator="admin"):
    """修改台账记录；若状态发生变化，自动登记一条状态流转记录。"""
    origin = fetch_asset_by_id(asset_id)
    if not origin:
        return {"success": False, "message": "台账记录不存在：%s" % asset_id}

    ok, message, fields = validate_asset_payload(payload, for_update=True)
    if not ok:
        return {"success": False, "message": message}

    update_asset_row(asset_id, fields)
    old_status = origin["current_status"]
    new_status = fields["current_status"]
    if old_status != new_status:
        append_status_flow(
            asset_id, old_status, new_status,
            "状态由「%s」变更为「%s」" % (translate_status(old_status), translate_status(new_status)),
            operator,
        )
    write_ops_journal(
        "设备台账", "修改", origin["asset_code"],
        "修改设备：%s" % fields["asset_name"], operator,
    )
    return {"success": True, "message": "台账修改成功"}


def remove_asset_record(asset_id, operator="admin"):
    """删除台账记录，同时留下操作日志。"""
    origin = fetch_asset_by_id(asset_id)
    if not origin:
        return {"success": False, "message": "台账记录不存在：%s" % asset_id}
    delete_asset_row(asset_id)
    write_ops_journal(
        "设备台账", "删除", origin["asset_code"],
        "删除设备：%s" % origin["asset_name"], operator,
    )
    return {"success": True, "message": "台账删除成功"}


def search_asset_page(keyword="", status_code="", category_code="", page=1, size=PAGE_SIZE):
    """台账分页检索，返回前端表格可直接消费的结构。"""
    rows, total, total_page = query_asset_paged(keyword, status_code, category_code, page, size)
    return {
        "rows": rows,
        "total": total,
        "total_page": total_page,
        "page": page,
        "size": size,
    }


def build_ledger_options():
    """返回台账表单需要的下拉选项（状态字典与分类字典）。"""
    return {
        "status_options": [{"code": k, "label": v} for k, v in ASSET_STATUS_DICT.items()],
        "category_options": [{"code": k, "label": v} for k, v in ASSET_CATEGORY_DICT.items()],
    }


def summarize_ledger_value():
    """统计台账资产原值总额与平均服役年限，供台账页顶部指标卡展示。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(1) AS amount, COALESCE(SUM(purchase_price),0) AS total_price, "
            "COALESCE(AVG(service_life),0) AS avg_life FROM md_asset_ledger"
        ).fetchone()
    finally:
        conn.close()
    return {
        "amount": row["amount"],
        "total_price": round(row["total_price"], 2),
        "avg_life": round(row["avg_life"], 1),
    }


# --------------------------------------------------------------------------
# 模块 5：mdm_idle.py
# 模块功能：闲置设备识别与设备状态管理业务层。实现按设备分类套用闲置 判定规则扫描长期闲置资产、闲置天数与闲置金额测算、闲置快照 归档，以及单台/批量设备状态切换（含流转登记与日志留痕）， 并输出状态流转趋势与科室闲置分布等统计图表数据
# --------------------------------------------------------------------------


# 默认闲置阈值：分类规则缺失时兜底使用（天）
DEFAULT_IDLE_THRESHOLD = 90

# 允许的手动状态切换目标（报废需走第 7 天审批流程，此处仅开放日常状态）
MANUAL_STATUS_TARGETS = ["IN_USE", "IDLE", "REPAIR", "STOCK"]


def today_text():
    """返回今天日期字符串（YYYY-MM-DD）。"""
    return datetime.date.today().strftime("%Y-%m-%d")


def _parse_date(text):
    """把日期字符串解析为 date 对象，解析失败返回 None。"""
    if not text:
        return None
    text = str(text)[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def ensure_idle_rules():
    """初始化闲置判定规则：为每个设备分类生成一条默认规则。"""
    conn = get_connection()
    try:
        for code in ASSET_CATEGORY_DICT:
            exist = conn.execute(
                "SELECT COUNT(1) AS cnt FROM md_idle_rule WHERE category_code=?", (code,)
            ).fetchone()
            if exist["cnt"] == 0:
                conn.execute(
                    "INSERT INTO md_idle_rule (category_code, threshold_days, risk_level, rule_desc) "
                    "VALUES (?,?,?,?)",
                    (code, DEFAULT_IDLE_THRESHOLD, 2, "%s默认闲置阈值 %d 天"
                     % (translate_category(code), DEFAULT_IDLE_THRESHOLD)),
                )
        conn.commit()
    finally:
        conn.close()
    return True


def fetch_idle_rules():
    """读取全部闲置判定规则，附加分类中文名称。"""
    ensure_idle_rules()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT rule_id, category_code, threshold_days, risk_level, rule_desc, enabled_flag "
            "FROM md_idle_rule ORDER BY rule_id"
        ).fetchall()
    finally:
        conn.close()
    result = []
    for item in [dict(r) for r in rows]:
        item["category_label"] = translate_category(item["category_code"])
        item["risk_label"] = RISK_LEVEL_DICT.get(item["risk_level"], "预警")
        result.append(item)
    return result


def save_idle_rule(category_code, threshold_days, risk_level, rule_desc=""):
    """新增或更新某分类的闲置判定规则。"""
    if category_code not in ASSET_CATEGORY_DICT:
        return {"success": False, "message": "设备分类编码不合法：%s" % category_code}
    if threshold_days < 0 or risk_level not in RISK_LEVEL_DICT:
        return {"success": False, "message": "阈值或风险等级取值不合法"}
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO md_idle_rule (category_code, threshold_days, risk_level, rule_desc) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(category_code) DO UPDATE SET "
            "threshold_days=excluded.threshold_days, risk_level=excluded.risk_level, "
            "rule_desc=excluded.rule_desc, updated_at=datetime('now','localtime')",
            (category_code, int(threshold_days), int(risk_level), rule_desc),
        )
        conn.commit()
    finally:
        conn.close()
    write_ops_journal(
        "闲置规则", "修改", category_code,
        "更新%s闲置阈值：%d 天，风险等级：%s"
        % (translate_category(category_code), int(threshold_days),
           RISK_LEVEL_DICT.get(int(risk_level), "")),
    )
    return {"success": True, "message": "闲置判定规则已保存"}


def calc_idle_days(asset_id, idle_since_text):
    """计算设备自进入闲置状态起已连续闲置的天数。"""
    start = _parse_date(idle_since_text)
    if start is None:
        return 0
    delta = datetime.date.today() - start
    return delta.days if delta.days > 0 else 0


def scan_idle_assets(operator="system"):
    """扫描全部闲置状态设备，按分类规则判定是否构成长期闲置。

    判定逻辑：
        1. 取当前状态为 IDLE 的设备；
        2. 以该设备最近一次状态流转时间作为闲置起点，计算连续闲置天数；
        3. 按设备分类套用 md_idle_rule 中的阈值，超出即判定为长期闲置；
        4. 识别结果写入 md_idle_snapshot 归档，供闲置趋势回溯。
    """
    ensure_idle_rules()
    rule_map = {}
    for rule in fetch_idle_rules():
        if rule["enabled_flag"] == 1:
            rule_map[rule["category_code"]] = rule

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT a.asset_id, a.asset_code, a.asset_name, a.asset_category, a.dept_name, "
            "a.purchase_price, a.custodian, a.location_text, a.current_status, a.purchase_date, "
            "(SELECT flow_time FROM md_status_flow f WHERE f.asset_id=a.asset_id "
            " ORDER BY f.flow_id DESC LIMIT 1) AS last_flow_time "
            "FROM md_asset_ledger a WHERE a.current_status='IDLE' ORDER BY a.asset_id"
        ).fetchall()
        idle_rows = [dict(r) for r in rows]
    finally:
        conn.close()

    today = today_text()
    result = []
    long_idle_amount = 0
    total_idle_value = 0.0
    for item in idle_rows:
        rule = rule_map.get(item["asset_category"])
        threshold = rule["threshold_days"] if rule else DEFAULT_IDLE_THRESHOLD
        risk = rule["risk_level"] if rule else 2
        # 闲置起点优先取最近一次状态流转时间，无流转记录时回退到采购入库日期
        since = item["last_flow_time"] or item["purchase_date"]
        idle_days = calc_idle_days(item["asset_id"], since)
        is_long = idle_days >= threshold
        value = float(item["purchase_price"] or 0)
        total_idle_value += value
        if is_long:
            long_idle_amount += 1
        result.append({
            "asset_id": item["asset_id"],
            "asset_code": item["asset_code"],
            "asset_name": item["asset_name"],
            "category_label": translate_category(item["asset_category"]),
            "dept_name": item["dept_name"],
            "custodian": item["custodian"],
            "location_text": item["location_text"],
            "idle_days": idle_days,
            "threshold_days": threshold,
            "is_long_idle": 1 if is_long else 0,
            "risk_level": risk if is_long else (1 if idle_days > 0 else 0),
            "risk_label": RISK_LEVEL_DICT.get(risk if is_long else (1 if idle_days > 0 else 0), "正常"),
            "idle_value": round(value, 2),
        })

    # 归档本轮扫描结果
    conn = get_connection()
    try:
        for row in result:
            conn.execute(
                "INSERT INTO md_idle_snapshot "
                "(asset_id, asset_code, idle_days, threshold_days, risk_level, idle_value, scan_date) "
                "VALUES (?,?,?,?,?,?,?)",
                (row["asset_id"], row["asset_code"], row["idle_days"], row["threshold_days"],
                 row["risk_level"], row["idle_value"], today),
            )
        conn.commit()
    finally:
        conn.close()

    write_ops_journal(
        "闲置识别", "扫描", "IDLE-SCAN",
        "扫描闲置设备 %d 台，其中长期闲置 %d 台" % (len(result), long_idle_amount), operator,
    )
    return {
        "scan_date": today,
        "idle_amount": len(result),
        "long_idle_amount": long_idle_amount,
        "total_idle_value": round(total_idle_value, 2),
        "rows": result,
    }


def switch_asset_status(asset_id, to_status, change_reason="", operator="admin"):
    """单台设备状态切换：校验 → 更新台账 → 登记流转 → 写日志。"""
    origin = fetch_asset_by_id(asset_id)
    if not origin:
        return {"success": False, "message": "台账记录不存在：%s" % asset_id}
    if to_status not in ASSET_STATUS_DICT:
        return {"success": False, "message": "目标状态编码不合法：%s" % to_status}
    if to_status not in MANUAL_STATUS_TARGETS:
        return {"success": False, "message": "目标状态需走专用流程：%s" % ASSET_STATUS_DICT.get(to_status)}
    old_status = origin["current_status"]
    if old_status == to_status:
        return {"success": False, "message": "设备已处于「%s」状态，无需切换" % translate_status(to_status)}

    update_asset_row(asset_id, {"current_status": to_status})
    append_status_flow(
        asset_id, old_status, to_status,
        change_reason or "状态由「%s」切换为「%s」"
        % (translate_status(old_status), translate_status(to_status)),
        operator,
    )
    write_ops_journal(
        "状态管理", "修改", origin["asset_code"],
        "状态切换：%s → %s（%s）"
        % (translate_status(old_status), translate_status(to_status), change_reason or "手工切换"),
        operator,
    )
    return {
        "success": True,
        "message": "状态已切换为「%s」" % translate_status(to_status),
        "from_status": old_status,
        "to_status": to_status,
    }


def batch_switch_status(asset_codes, to_status, change_reason="", operator="admin"):
    """批量切换设备状态，逐台执行并统计成败。"""
    if not asset_codes:
        return {"success": False, "message": "未选择任何设备"}
    done, failed = 0, []
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT asset_id, asset_code FROM md_asset_ledger WHERE asset_code IN (%s)"
            % ",".join(["?"] * len(asset_codes)),
            list(asset_codes),
        ).fetchall()
        id_map = {r["asset_code"]: r["asset_id"] for r in rows}
    finally:
        conn.close()
    for code in asset_codes:
        asset_id = id_map.get(code)
        if not asset_id:
            failed.append(code)
            continue
        res = switch_asset_status(asset_id, to_status, change_reason, operator)
        if res["success"]:
            done += 1
        else:
            failed.append(code)
    return {
        "success": done > 0,
        "message": "批量切换完成：成功 %d 台，失败 %d 台" % (done, len(failed)),
        "done": done,
        "failed": failed,
    }


def fetch_status_flow_trend(days=30):
    """统计近 N 天的状态流转次数趋势（按日期分组），用于状态统计折线图。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT substr(flow_time,1,10) AS day_text, COUNT(1) AS flow_count "
            "FROM md_status_flow "
            "WHERE flow_time >= datetime('now','localtime','-%d days') "
            "GROUP BY day_text ORDER BY day_text" % int(days)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def fetch_dept_idle_distribution():
    """按科室统计闲置设备数量与闲置金额，用于科室闲置分布柱状图。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT dept_name, COUNT(1) AS idle_amount, "
            "ROUND(COALESCE(SUM(purchase_price),0),2) AS idle_value "
            "FROM md_asset_ledger WHERE current_status='IDLE' "
            "GROUP BY dept_name ORDER BY idle_amount DESC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def build_status_statistics():
    """组装状态管理页所需的全部统计图表数据。"""
    scan = scan_idle_assets()
    conn = get_connection()
    try:
        status_rows = conn.execute(
            "SELECT current_status AS status_code, COUNT(1) AS amount "
            "FROM md_asset_ledger GROUP BY current_status ORDER BY amount DESC"
        ).fetchall()
        dept_rows = conn.execute(
            "SELECT dept_name, current_status, COUNT(1) AS amount "
            "FROM md_asset_ledger GROUP BY dept_name, current_status"
        ).fetchall()
    finally:
        conn.close()

    status_list = []
    for item in [dict(r) for r in status_rows]:
        item["status_label"] = translate_status(item["status_code"])
        status_list.append(item)

    dept_map = {}
    for item in [dict(r) for r in dept_rows]:
        node = dept_map.setdefault(item["dept_name"], {"dept_name": item["dept_name"], "detail": {}})
        node["detail"][item["current_status"]] = item["amount"]

    return {
        "status_distribution": status_list,
        "status_options": [{"code": k, "label": v} for k, v in ASSET_STATUS_DICT.items()],
        "idle_scan": scan,
        "flow_trend": fetch_status_flow_trend(30),
        "dept_idle": fetch_dept_idle_distribution(),
        "dept_status_matrix": list(dept_map.values()),
        "idle_rules": fetch_idle_rules(),
    }


def build_idle_brief():
    """提供给运维总览看板的闲置概览数据（轻量版，不落快照）。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(1) AS idle_amount, ROUND(COALESCE(SUM(purchase_price),0),2) AS idle_value "
            "FROM md_asset_ledger WHERE current_status='IDLE'"
        ).fetchone()
        long_row = conn.execute(
            "SELECT COUNT(1) AS long_amount FROM md_idle_snapshot "
            "WHERE risk_level >= 2 AND scan_date=?"
            , (today_text(),)
        ).fetchone()
    finally:
        conn.close()
    return {
        "idle_amount": row["idle_amount"] if row else 0,
        "idle_value": row["idle_value"] if row else 0.0,
        "long_idle_amount": long_row["long_amount"] if long_row else 0,
    }


# --------------------------------------------------------------------------
# 模块 6：app.py
# 模块功能：Flask 主程序入口。完成应用创建、数据库初始化、演示数据灌入， 注册运维总览看板、设备台账 CRUD 与设备状态管理三套页面路由， 并提供台账增删改查、闲置识别扫描、状态切换等 RESTful 接口， 供前端表格、弹窗与 ECharts 图表异步取数
# --------------------------------------------------------------------------


app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


@app.before_request
def before_first_request_hook():
    """占位钩子：后续第 8 天接入登录鉴权时在此处做会话校验。"""
    return None


@app.route("/")
def index_redirect():
    """根路径跳转到运维总览看板。"""
    return redirect(url_for("panel_overview"))


@app.route("/md/overview")
def panel_overview():
    """运维总览看板页面（第 12 天将在此补充图表联动与下钻）。"""
    write_ops_journal("运维总览", "查询", "PANEL-OVERVIEW", "打开运维总览看板页面")
    return render_template(
        "panel_overview.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/md/assets")
def panel_assets():
    """设备台账管理页面：完整增删改查（第 2 天实现）。"""
    return render_template(
        "panel_assets.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


# ==================================================================
# 以下为第 2 天新增：设备台账 CRUD 接口
# ==================================================================

@app.route("/api/md/assets", methods=["GET", "POST"])
def api_asset_collection():
    """台账集合接口：GET 分页检索，POST 新增记录。"""
    if request.method == "GET":
        page = int(request.args.get("page", 1))
        size = int(request.args.get("size", 15))
        result = search_asset_page(
            keyword=request.args.get("keyword", ""),
            status_code=request.args.get("status_code", ""),
            category_code=request.args.get("category_code", ""),
            page=page,
            size=size,
        )
        result["result"] = "OK"
        result["value_summary"] = summarize_ledger_value()
        return jsonify(result)

    payload = request.get_json(silent=True) or request.form.to_dict()
    outcome = create_asset_record(payload, operator="admin")
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/md/assets/<int:asset_id>", methods=["GET", "PUT", "DELETE"])
def api_asset_item(asset_id):
    """台账单条接口：GET 详情，PUT 修改，DELETE 删除。"""
    if request.method == "GET":
        detail = fetch_asset_by_id(asset_id)
        if not detail:
            return jsonify({"result": "FAIL", "message": "记录不存在"})
        return jsonify({
            "result": "OK",
            "detail": detail,
            "status_flow": fetch_status_flow(asset_id),
        })

    if request.method == "PUT":
        payload = request.get_json(silent=True) or request.form.to_dict()
        outcome = modify_asset_record(asset_id, payload, operator="admin")
        outcome["result"] = "OK" if outcome.get("success") else "FAIL"
        return jsonify(outcome)

    outcome = remove_asset_record(asset_id, operator="admin")
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/md/options")
def api_asset_options():
    """台账下拉选项接口：状态字典与分类字典。"""
    return jsonify({"result": "OK", "options": build_ledger_options()})


@app.route("/api/md/journals")
def api_recent_journals():
    """最近运维操作日志接口，供台账页侧栏展示。"""
    return jsonify({"result": "OK", "journals": fetch_latest_journals(15)})


@app.route("/md/status")
def panel_status():
    """设备状态管理页面：闲置识别、状态切换与状态统计图表（第 3 天）。"""
    write_ops_journal("状态管理", "查询", "PANEL-STATUS", "打开设备状态管理页面")
    return render_template(
        "panel_status.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/md/overview")
def api_overview():
    """看板数据接口：返回设备总数、状态分布、分类分布、闲置概览与近期台账清单。"""
    payload = build_overview_payload()
    payload["idle_brief"] = build_idle_brief()
    payload["result"] = "OK"
    return jsonify(payload)


# ==================================================================
# 以下为第 3 天新增：闲置设备识别与设备状态管理接口
# ==================================================================

@app.route("/api/md/status/stat")
def api_status_statistics():
    """状态统计接口：状态分布、闲置扫描结果、流转趋势、科室闲置分布与规则。"""
    payload = build_status_statistics()
    payload["result"] = "OK"
    return jsonify(payload)


@app.route("/api/md/idle/scan", methods=["GET", "POST"])
def api_idle_scan():
    """闲置识别扫描接口：按分类规则扫描闲置设备并归档快照。"""
    scan = scan_idle_assets(operator="admin")
    scan["result"] = "OK"
    return jsonify(scan)


@app.route("/api/md/status/switch", methods=["POST"])
def api_status_switch():
    """单台设备状态切换接口。"""
    payload = request.get_json(silent=True) or request.form.to_dict()
    asset_id = int(payload.get("asset_id") or 0)
    if not asset_id:
        return jsonify({"result": "FAIL", "success": False, "message": "缺少 asset_id"})
    outcome = switch_asset_status(
        asset_id,
        (payload.get("to_status") or "").strip(),
        (payload.get("change_reason") or "").strip(),
        operator="admin",
    )
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/md/status/batch", methods=["POST"])
def api_status_batch():
    """批量设备状态切换接口。"""
    payload = request.get_json(silent=True) or {}
    outcome = batch_switch_status(
        payload.get("asset_codes") or [],
        (payload.get("to_status") or "").strip(),
        (payload.get("change_reason") or "").strip(),
        operator="admin",
    )
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/md/idle/rules", methods=["GET", "POST"])
def api_idle_rules():
    """闲置判定规则接口：GET 读取规则清单，POST 保存某分类规则。"""
    if request.method == "GET":
        return jsonify({"result": "OK", "rules": fetch_idle_rules()})
    payload = request.get_json(silent=True) or {}
    outcome = save_idle_rule(
        (payload.get("category_code") or "").strip(),
        int(payload.get("threshold_days") or DEFAULT_IDLE_THRESHOLD),
        int(payload.get("risk_level") or 2),
        (payload.get("rule_desc") or "").strip(),
    )
    outcome["result"] = "OK" if outcome.get("success") else "FAIL"
    return jsonify(outcome)


@app.route("/api/md/health")
def api_health():
    """系统健康检查接口，用于部署后快速验证服务是否可用。"""
    return jsonify({
        "result": "OK",
        "software": SOFTWARE_FULL_NAME,
        "version": SOFTWARE_VERSION,
    })


def bootstrap_application():
    """应用启动引导：建表 + 灌入演示数据 + 初始化闲置判定规则。"""
    init_database()
    seed_demo_assets()
    ensure_idle_rules()


if __name__ == "__main__":
    bootstrap_application()
    print("=" * 62)
    print("%s %s 启动中..." % (SOFTWARE_FULL_NAME, SOFTWARE_VERSION))
    print("运维总览： http://127.0.0.1:%d/md/overview" % PORT)
    print("设备台账： http://127.0.0.1:%d/md/assets" % PORT)
    print("状态管理： http://127.0.0.1:%d/md/status" % PORT)
    print("=" * 62)
    app.run(host=HOST, port=PORT, debug=DEBUG)
