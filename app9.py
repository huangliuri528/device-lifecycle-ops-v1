# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-29（第 9 天 / 共 15 天）
# 模块功能：本文件仅含该软件第 9 天新增与修改的源代码，不含第 1~8 天已有代码
#           本次迭代主题：供应商与合同管理模块 + 供应商合同页面
# 依赖包：Flask==3.0.3
# 启动命令：python app.py （默认 http://127.0.0.1:5001/md/overview）
# 接入说明：以下内容为当日增量，需与前面各天的代码合并后运行——
#           新增模块按文件名放入工程目录；建表脚本增量追加进对应 .sql；
#           主程序增量片段插入 app.py 中 Flask 实例之后；前端资源按文末清单
#           放入 templates/ 与 static/ 目录


# --------------------------------------------------------------------------
# 第 9 天新增业务模块 1：mdm_vendor.py（完整源码）
# --------------------------------------------------------------------------

# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-29（第 9 天 / 共 15 天）
# 模块功能：供应商与合同管理模块。实现设备厂商、代理商与第三方服务商档案
#           的登记维护，采购、维保、租赁、校准与服务五类合同的签订登记，
#           合同款项的付款节点拆分与实际执行登记，并支持合同到期扫描、
#           到期等级预警与供应商履约评分评级自动回算；输出供应商等级分布、
#           合同类型分布、合同状态分布、供应商合同金额排行与近 6 个月合同
#           签订趋势五类统计图表，形成「建档案 → 签合同 → 分期执行 →
#           到期复评 → 履约评级」的供应商全生命周期管理闭环
# 依赖包：sqlite3（Python 内置）、datetime（Python 内置）

import datetime

from mdm_dal import get_connection, write_ops_journal

# ------------------------------------------------------------------
# 供应商类型字典：区分设备原厂、区域代理与第三方服务商
# ------------------------------------------------------------------
VENDOR_TYPE_DICT = {
    "MAKER": "设备厂商",
    "AGENT": "区域代理",
    "SERVICE": "第三方服务商",
}

# 供应商履约评级字典：由付款节点执行情况与合同到期情况自动回算
VENDOR_GRADE_DICT = {
    "A": "A 级优质",
    "B": "B 级良好",
    "C": "C 级观察",
    "D": "D 级警示",
}

# 履约评级阈值（评分下限由高到低匹配）
VENDOR_GRADE_THRESHOLD = (
    ("A", 90),
    ("B", 75),
    ("C", 60),
    ("D", 0),
)

# 供应商合作状态字典
VENDOR_STATE_DICT = {
    "RUN": "合作中",
    "PAUSE": "暂停合作",
    "BLACK": "列入黑名单",
}

# 合同类型字典
CONTRACT_TYPE_DICT = {
    "PURCHASE": "设备采购",
    "MAINTAIN": "维保服务",
    "LEASE": "设备租赁",
    "CALIB": "计量校准",
    "SERVICE": "技术支持",
}

# 付款方式字典
PAY_METHOD_DICT = {
    "ONCE": "一次性付清",
    "STAGE": "分期付款",
    "PERIOD": "周期结算",
}

# 合同状态字典：由登记、节点执行与到期扫描共同维护
CONTRACT_STATE_DICT = {
    "DRAFT": "草稿待签",
    "RUN": "履行中",
    "EXPIRE": "已到期",
    "END": "已结清",
    "VOID": "已作废",
}

# 付款节点状态字典
STAGE_STATE_DICT = {
    "WAIT": "待执行",
    "DONE": "已执行",
    "OVERDUE": "已逾期",
}

# 合同到期等级字典：按距到期的剩余天数划分
CONTRACT_DUE_DICT = {
    "OVERDUE": "已逾期",
    "URGENT": "15天内到期",
    "SOON": "30天内到期",
    "NORMAL": "有效期充足",
}

# 到期等级判定阈值（剩余天数由少到多匹配）
CONTRACT_DUE_THRESHOLD = (
    ("URGENT", 15),
    ("SOON", 30),
)

# 合同付款节点模板：登记合同时按付款方式自动拆分款项节点
STAGE_TEMPLATE_DICT = {
    "ONCE": (("全款", 100),),
    "STAGE": (("预付款", 30), ("到货验收款", 50), ("质保金", 20)),
    "PERIOD": (("首期款", 25), ("中期款", 25), ("三期款", 25), ("尾款", 25)),
}

# 履约评分扣减权重：逾期节点、逾期合同与延期天数各自扣分
SCORE_DEDUCT_RULES = {
    "OVERDUE_STAGE": 5,     # 每个逾期付款节点扣分
    "OVERDUE_CONTRACT": 8,  # 每份逾期未结清合同扣分
    "DELAY_DAY": 0.2,       # 每延期一天扣分
}

# 分页尺寸：供应商档案与合同明细各自独立分页
VENDOR_PAGE_SIZE = 8
CONTRACT_PAGE_SIZE = 10

# 演示供应商名称列表，用于首日灌入演示档案
DEMO_VENDOR_LIST = (
    ("迈瑞生物医疗电子股份有限公司", "MAKER", "影像与监护设备"),
    ("西门子医疗系统有限公司", "MAKER", "大型影像设备"),
    ("江苏康尚医疗器械有限公司", "AGENT", "检验分析设备"),
    ("南京医工运维服务有限公司", "SERVICE", "全院维保外包"),
    ("国药集团江苏器械有限公司", "AGENT", "耗材与配件供应"),
)


# ------------------------------------------------------------------
# 日期与编码工具
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


def judge_due_level(due_days):
    """按合同剩余天数判定到期等级编码。"""
    if due_days is None:
        return "NORMAL"
    if due_days < 0:
        return "OVERDUE"
    for level_code, ceiling in CONTRACT_DUE_THRESHOLD:
        if due_days <= ceiling:
            return level_code
    return "NORMAL"


def judge_grade_code(score_value):
    """按履约评分判定供应商评级编码。"""
    for grade_code, floor in VENDOR_GRADE_THRESHOLD:
        if score_value >= floor:
            return grade_code
    return "D"


def build_vendor_code(seq_no):
    """生成供应商编码：VEN-四位序号。"""
    return "VEN-%04d" % seq_no


def build_contract_code(seq_no):
    """生成合同编号：CT-年份-四位序号。"""
    return "CT-%d-%04d" % (today_date().year, seq_no)


def build_stage_code(contract_code, seq_no):
    """生成付款节点编号：CS-合同序号-节点序号。"""
    tail = contract_code.split("-")[-1] if contract_code else "0000"
    return "CS-%s-%02d" % (tail, seq_no)


def next_seq_no(table_name):
    """读取指定业务表的记录数并加一，用于拼装业务编码。"""
    conn = get_connection()
    try:
        amount = conn.execute(
            "SELECT COUNT(1) AS c FROM %s" % table_name).fetchone()["c"]
    finally:
        conn.close()
    return amount + 1


def fetch_vendor_row(vendor_code):
    """按供应商编码读取档案记录，未找到返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_vendor_profile WHERE vendor_code=?",
            (vendor_code,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def fetch_contract_row(contract_code):
    """按合同编号读取合同记录，未找到返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_contract_info WHERE contract_code=?",
            (contract_code,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def refresh_vendor_statistics(vendor_code):
    """回算某供应商的累计合同份数与累计合同金额。

    :param vendor_code: 供应商编码
    :return: 更新后的（合同份数，合同金额）元组
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(1) AS c, COALESCE(SUM(sign_amount),0) AS s "
            "FROM md_contract_info WHERE vendor_code=? AND contract_state<>'VOID'",
            (vendor_code,)).fetchone()
        amount = row["c"]
        total = round(row["s"], 2)
        conn.execute(
            "UPDATE md_vendor_profile SET contract_count=?, contract_amount=?, "
            "updated_at=? WHERE vendor_code=?",
            (amount, total, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             vendor_code))
        conn.commit()
    finally:
        conn.close()
    return amount, total


# ------------------------------------------------------------------
# 供应商档案登记
# ------------------------------------------------------------------
def save_vendor(vendor_code, vendor_name, vendor_type="MAKER", license_no="",
                contact_name="", contact_phone="", supply_scope="",
                cooperate_since="", vendor_state="RUN", address_text="",
                remark_text="", operator="admin"):
    """登记或更新一条供应商档案。

    传入已存在的供应商编码时执行更新，否则按新编码建档；留空编码时由系统
    按序号自动生成，保证同一家供应商在合同登记时可被唯一引用。

    :return: 含 success 与 message 的执行结果字典
    """
    vendor_name = (vendor_name or "").strip()
    if not vendor_name:
        return {"success": False, "message": "供应商名称不能为空"}
    if vendor_type not in VENDOR_TYPE_DICT:
        return {"success": False, "message": "供应商类型 %s 不合法" % vendor_type}
    if vendor_state not in VENDOR_STATE_DICT:
        return {"success": False, "message": "合作状态 %s 不合法" % vendor_state}
    if (contact_phone or "").strip() and len(contact_phone.strip()) > 20:
        return {"success": False, "message": "联系电话长度不能超过 20 位"}

    vendor_code = (vendor_code or "").strip()
    conn = get_connection()
    try:
        if vendor_code:
            exist = conn.execute(
                "SELECT vendor_id FROM md_vendor_profile WHERE vendor_code=?",
                (vendor_code,)).fetchone()
        else:
            exist = None
            vendor_code = build_vendor_code(next_seq_no("md_vendor_profile"))
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if exist is None:
            conn.execute(
                "INSERT INTO md_vendor_profile "
                "(vendor_code, vendor_name, vendor_type, license_no, contact_name, "
                "contact_phone, supply_scope, grade_code, score_value, "
                "contract_count, contract_amount, cooperate_since, vendor_state, "
                "address_text, remark_text, updated_at) "
                "VALUES (?,?,?,?,?,?,?,'B',0,0,0,?,?,?,?,?)",
                (vendor_code, vendor_name, vendor_type, (license_no or "").strip(),
                 (contact_name or "").strip(), (contact_phone or "").strip(),
                 (supply_scope or "").strip(), (cooperate_since or "").strip(),
                 vendor_state, (address_text or "").strip(),
                 (remark_text or "").strip(), stamp))
            action = "建档"
        else:
            conn.execute(
                "UPDATE md_vendor_profile SET vendor_name=?, vendor_type=?, "
                "license_no=?, contact_name=?, contact_phone=?, supply_scope=?, "
                "cooperate_since=?, vendor_state=?, address_text=?, "
                "remark_text=?, updated_at=? WHERE vendor_code=?",
                (vendor_name, vendor_type, (license_no or "").strip(),
                 (contact_name or "").strip(), (contact_phone or "").strip(),
                 (supply_scope or "").strip(), (cooperate_since or "").strip(),
                 vendor_state, (address_text or "").strip(),
                 (remark_text or "").strip(), stamp, vendor_code))
            action = "更新"
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("供应商合同", action, vendor_code,
                      "%s供应商档案：%s（%s）"
                      % (action, vendor_name, VENDOR_TYPE_DICT[vendor_type]),
                      operator)
    return {"success": True,
            "message": "供应商档案%s成功，编码 %s" % (action, vendor_code),
            "vendor_code": vendor_code}


# ------------------------------------------------------------------
# 合同签订登记
# ------------------------------------------------------------------
def save_contract(contract_code, contract_name, vendor_code, contract_type="PURCHASE",
                  sign_amount=0, sign_date="", start_date="", end_date="",
                  warranty_months=12, pay_method="STAGE", file_no="",
                  contract_state="RUN", operator="admin"):
    """登记一份设备采购或技术服务合同，并按付款方式自动拆分付款节点。

    :return: 含 success、message 与生成的付款节点条数的结果字典
    """
    contract_name = (contract_name or "").strip()
    if not contract_name:
        return {"success": False, "message": "合同名称不能为空"}
    vendor = fetch_vendor_row((vendor_code or "").strip())
    if vendor is None:
        return {"success": False, "message": "供应商编码 %s 不存在" % vendor_code}
    if vendor["vendor_state"] == "BLACK":
        return {"success": False, "message": "该供应商已列入黑名单，不可新签合同"}
    if contract_type not in CONTRACT_TYPE_DICT:
        return {"success": False, "message": "合同类型 %s 不合法" % contract_type}
    if pay_method not in PAY_METHOD_DICT:
        return {"success": False, "message": "付款方式 %s 不合法" % pay_method}
    if contract_state not in CONTRACT_STATE_DICT:
        return {"success": False, "message": "合同状态 %s 不合法" % contract_state}
    try:
        amount = float(sign_amount)
    except (TypeError, ValueError):
        return {"success": False, "message": "签约金额必须为数字"}
    if amount <= 0:
        return {"success": False, "message": "签约金额必须大于 0"}
    sign_day = parse_date_text(sign_date) or today_date()
    begin_day = parse_date_text(start_date) or sign_day
    finish_day = parse_date_text(end_date)
    if finish_day is None:
        return {"success": False, "message": "履约结束日期格式应为 YYYY-MM-DD"}
    if finish_day < begin_day:
        return {"success": False, "message": "履约结束日期不能早于开始日期"}
    try:
        warranty = int(warranty_months)
    except (TypeError, ValueError):
        warranty = 12
    if warranty < 0:
        return {"success": False, "message": "质保月数不能为负数"}

    contract_code = (contract_code or "").strip()
    conn = get_connection()
    stage_amount = 0
    try:
        if contract_code:
            exist = conn.execute(
                "SELECT contract_id FROM md_contract_info WHERE contract_code=?",
                (contract_code,)).fetchone()
        else:
            exist = None
            contract_code = build_contract_code(next_seq_no("md_contract_info"))
        due_days = (finish_day - today_date()).days
        if exist is None:
            conn.execute(
                "INSERT INTO md_contract_info "
                "(contract_code, contract_name, vendor_code, vendor_name, "
                "contract_type, sign_amount, settle_amount, sign_date, start_date, "
                "end_date, warranty_months, pay_method, file_no, contract_state, "
                "due_days, operator_name) VALUES (?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?)",
                (contract_code, contract_name, vendor["vendor_code"],
                 vendor["vendor_name"], contract_type, round(amount, 2),
                 date_to_text(sign_day), date_to_text(begin_day),
                 date_to_text(finish_day), warranty, pay_method,
                 (file_no or "").strip(), contract_state, due_days, operator))
            # 按付款方式模板拆分付款节点，节点日期在履约期内均匀铺开
            template = STAGE_TEMPLATE_DICT[pay_method]
            span_days = max(1, (finish_day - begin_day).days)
            for index, (stage_name, percent) in enumerate(template):
                stage_code = build_stage_code(contract_code, index + 1)
                plan_day = begin_day + datetime.timedelta(
                    days=int(span_days * (index + 1) / (len(template) + 1)))
                conn.execute(
                    "INSERT INTO md_contract_stage "
                    "(stage_code, contract_code, stage_name, plan_amount, "
                    "plan_date, stage_state, operator_name) VALUES (?,?,?,?,?,?,?)",
                    (stage_code, contract_code, stage_name,
                     round(amount * percent / 100.0, 2), date_to_text(plan_day),
                     "WAIT", operator))
                stage_amount += 1
            action = "签订"
        else:
            conn.execute(
                "UPDATE md_contract_info SET contract_name=?, vendor_code=?, "
                "vendor_name=?, contract_type=?, sign_amount=?, sign_date=?, "
                "start_date=?, end_date=?, warranty_months=?, pay_method=?, "
                "file_no=?, contract_state=?, due_days=? WHERE contract_code=?",
                (contract_name, vendor["vendor_code"], vendor["vendor_name"],
                 contract_type, round(amount, 2), date_to_text(sign_day),
                 date_to_text(begin_day), date_to_text(finish_day), warranty,
                 pay_method, (file_no or "").strip(), contract_state, due_days,
                 contract_code))
            action = "更新"
        conn.commit()
    finally:
        conn.close()
    refresh_vendor_statistics(vendor["vendor_code"])
    write_ops_journal("供应商合同", action, contract_code,
                      "%s合同：%s，签约金额 %.2f 元，拆付款节点 %d 条"
                      % (action, contract_name, amount, stage_amount), operator)
    return {"success": True,
            "message": "合同%s成功，编号 %s，已生成付款节点 %d 条"
                       % (action, contract_code, stage_amount),
            "contract_code": contract_code, "stage_amount": stage_amount}


# ------------------------------------------------------------------
# 付款节点执行
# ------------------------------------------------------------------
def execute_contract_stage(stage_code, actual_amount=None, actual_date="",
                           remark_text="", operator="admin"):
    """登记一次合同付款节点的实际执行情况并回写合同已结算金额。

    实际付款日期晚于计划日期时按延期天数记账，用于供应商履约评分扣减；
    节点全部执行完毕后合同自动转为已结清状态。

    :return: 含 success 与 message 的执行结果字典
    """
    stage_code = (stage_code or "").strip()
    if not stage_code:
        return {"success": False, "message": "付款节点编号不能为空"}
    pay_day = parse_date_text(actual_date) or today_date()
    if pay_day > today_date():
        return {"success": False, "message": "实际付款日期不能晚于今天"}

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM md_contract_stage WHERE stage_code=?",
            (stage_code,)).fetchone()
        if row is None:
            return {"success": False, "message": "付款节点 %s 不存在" % stage_code}
        stage = dict(row)
        if stage["stage_state"] == "DONE":
            return {"success": False, "message": "该付款节点已执行，不可重复登记"}
        try:
            amount = float(actual_amount) if actual_amount not in (None, "") \
                else float(stage["plan_amount"])
        except (TypeError, ValueError):
            return {"success": False, "message": "实际付款金额必须为数字"}
        if amount < 0:
            return {"success": False, "message": "实际付款金额不能为负数"}
        plan_day = parse_date_text(stage["plan_date"])
        delay_days = (pay_day - plan_day).days if plan_day else 0
        conn.execute(
            "UPDATE md_contract_stage SET actual_amount=?, actual_date=?, "
            "stage_state='DONE', delay_days=?, remark_text=?, operator_name=? "
            "WHERE stage_code=?",
            (round(amount, 2), date_to_text(pay_day), delay_days,
             (remark_text or "").strip(), operator, stage_code))
        total_row = conn.execute(
            "SELECT COALESCE(SUM(actual_amount),0) AS s, COUNT(1) AS c, "
            "SUM(CASE WHEN stage_state='DONE' THEN 1 ELSE 0 END) AS d "
            "FROM md_contract_stage WHERE contract_code=?",
            (stage["contract_code"],)).fetchone()
        settle_amount = round(total_row["s"], 2)
        conn.execute(
            "UPDATE md_contract_info SET settle_amount=? WHERE contract_code=?",
            (settle_amount, stage["contract_code"]))
        if total_row["c"] == total_row["d"]:
            conn.execute(
                "UPDATE md_contract_info SET contract_state='END' "
                "WHERE contract_code=? AND contract_state<>'VOID'",
                (stage["contract_code"],))
        conn.commit()
    finally:
        conn.close()
    tip = "，延期 %d 天" % delay_days if delay_days > 0 else ""
    write_ops_journal("供应商合同", "付款", stage_code,
                      "执行付款节点 %s：%.2f 元%s"
                      % (stage["stage_name"], amount, tip), operator)
    return {"success": True,
            "message": "付款节点执行成功，金额 %.2f 元%s" % (amount, tip),
            "delay_days": delay_days}


# ------------------------------------------------------------------
# 到期扫描与履约评级
# ------------------------------------------------------------------
def scan_contract_expire():
    """扫描全部履行中合同，刷新剩余天数并把已过期的合同置为已到期。

    同步把超过计划日期仍未执行的付款节点标记为逾期，供履约评分扣减使用。

    :return: 本次新增到期的合同条数
    """
    conn = get_connection()
    expire_amount = 0
    try:
        rows = conn.execute(
            "SELECT contract_code, end_date, contract_state FROM md_contract_info "
            "WHERE contract_state IN ('DRAFT','RUN','EXPIRE')").fetchall()
        today = today_date()
        for item in rows:
            finish_day = parse_date_text(item["end_date"])
            if finish_day is None:
                continue
            due_days = (finish_day - today).days
            if due_days < 0 and item["contract_state"] != "EXPIRE":
                conn.execute(
                    "UPDATE md_contract_info SET contract_state='EXPIRE', "
                    "due_days=? WHERE contract_code=?",
                    (due_days, item["contract_code"]))
                expire_amount += 1
            else:
                conn.execute(
                    "UPDATE md_contract_info SET due_days=? WHERE contract_code=?",
                    (due_days, item["contract_code"]))
        conn.execute(
            "UPDATE md_contract_stage SET stage_state='OVERDUE' "
            "WHERE stage_state='WAIT' AND plan_date<>'' AND plan_date<?",
            (date_to_text(today),))
        conn.commit()
    finally:
        conn.close()
    if expire_amount:
        write_ops_journal("供应商合同", "扫描", "CONTRACT-EXPIRE-SCAN",
                          "合同到期扫描完成，新增到期合同 %d 份" % expire_amount)
    return expire_amount


def evaluate_vendor_grade(operator="admin"):
    """按付款节点与合同执行情况重算全部供应商的履约评分与评级。

    评分规则：满分 100 分，每个逾期付款节点扣 5 分，每份逾期未结清合同扣
    8 分，每个已执行节点的延期天数按每天 0.2 分累计扣减，扣完为止。

    :return: 本次评级的供应商数量
    """
    scan_contract_expire()
    conn = get_connection()
    try:
        vendors = conn.execute(
            "SELECT vendor_code, vendor_name FROM md_vendor_profile").fetchall()
        result = []
        for item in vendors:
            vendor_code = item["vendor_code"]
            overdue_stage = conn.execute(
                "SELECT COUNT(1) AS c FROM md_contract_stage "
                "WHERE stage_state='OVERDUE' AND contract_code IN "
                "(SELECT contract_code FROM md_contract_info WHERE vendor_code=?)",
                (vendor_code,)).fetchone()["c"]
            overdue_contract = conn.execute(
                "SELECT COUNT(1) AS c FROM md_contract_info WHERE vendor_code=? "
                "AND contract_state='EXPIRE' AND settle_amount<sign_amount",
                (vendor_code,)).fetchone()["c"]
            delay_row = conn.execute(
                "SELECT COALESCE(SUM(delay_days),0) AS s FROM md_contract_stage "
                "WHERE stage_state='DONE' AND delay_days>0 AND contract_code IN "
                "(SELECT contract_code FROM md_contract_info WHERE vendor_code=?)",
                (vendor_code,)).fetchone()
            delay_days = delay_row["s"] or 0
            deduct = (overdue_stage * SCORE_DEDUCT_RULES["OVERDUE_STAGE"]
                      + overdue_contract * SCORE_DEDUCT_RULES["OVERDUE_CONTRACT"]
                      + delay_days * SCORE_DEDUCT_RULES["DELAY_DAY"])
            score_value = round(max(0.0, 100.0 - deduct), 1)
            grade_code = judge_grade_code(score_value)
            conn.execute(
                "UPDATE md_vendor_profile SET score_value=?, grade_code=?, "
                "updated_at=? WHERE vendor_code=?",
                (score_value, grade_code,
                 datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 vendor_code))
            result.append({"vendor_code": vendor_code,
                           "score_value": score_value,
                           "grade_code": grade_code})
        conn.commit()
    finally:
        conn.close()
    write_ops_journal("供应商合同", "评级", "VENDOR-EVALUATE",
                      "供应商履约评级重算完成，覆盖 %d 家供应商" % len(result),
                      operator)
    return len(result)


# ------------------------------------------------------------------
# 查询与统计
# ------------------------------------------------------------------
def fetch_vendors(keyword="", vendor_type="", grade_code="",
                  vendor_state="", page=1):
    """按组合条件分页查询供应商档案，并附带累计合同与评级文案。"""
    where = []
    params = []
    if keyword:
        where.append("(vendor_code LIKE ? OR vendor_name LIKE ? OR "
                     "supply_scope LIKE ? OR contact_name LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like, like, like])
    if vendor_type:
        where.append("vendor_type=?")
        params.append(vendor_type)
    if grade_code:
        where.append("grade_code=?")
        params.append(grade_code)
    if vendor_state:
        where.append("vendor_state=?")
        params.append(vendor_state)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    if page_no < 1:
        page_no = 1
    offset = (page_no - 1) * VENDOR_PAGE_SIZE

    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_vendor_profile" + clause,
            params).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM md_vendor_profile" + clause +
            " ORDER BY score_value DESC, vendor_id ASC LIMIT ? OFFSET ?",
            params + [VENDOR_PAGE_SIZE, offset]).fetchall()
    finally:
        conn.close()
    records = []
    for item in rows:
        row = dict(item)
        row["type_label"] = VENDOR_TYPE_DICT.get(row["vendor_type"], row["vendor_type"])
        row["grade_label"] = VENDOR_GRADE_DICT.get(row["grade_code"], row["grade_code"])
        row["state_label"] = VENDOR_STATE_DICT.get(row["vendor_state"], row["vendor_state"])
        records.append(row)
    return {
        "records": records,
        "total": total,
        "page": page_no,
        "page_size": VENDOR_PAGE_SIZE,
        "page_amount": max(1, (total + VENDOR_PAGE_SIZE - 1) // VENDOR_PAGE_SIZE),
    }


def fetch_contracts(keyword="", vendor_code="", contract_type="",
                    contract_state="", due_level="", page=1):
    """按组合条件分页查询合同明细，并附带到期等级与结算进度。"""
    where = []
    params = []
    if keyword:
        where.append("(contract_code LIKE ? OR contract_name LIKE ? OR "
                     "vendor_name LIKE ?)")
        like = "%%%s%%" % keyword
        params.extend([like, like, like])
    if vendor_code:
        where.append("vendor_code=?")
        params.append(vendor_code)
    if contract_type:
        where.append("contract_type=?")
        params.append(contract_type)
    if contract_state:
        where.append("contract_state=?")
        params.append(contract_state)
    today = today_date()
    if due_level and due_level in CONTRACT_DUE_DICT:
        # 到期等级在 SQL 层按履约结束日期区间过滤，保证分页总数与筛选一致；
        # 已结清与已作废合同不参与到期预警
        where.append("contract_state IN ('DRAFT','RUN','EXPIRE')")
        if due_level == "OVERDUE":
            where.append("end_date<>'' AND end_date<?")
            params.append(date_to_text(today))
        elif due_level == "URGENT":
            where.append("end_date>=? AND end_date<=?")
            params.append(date_to_text(today))
            params.append(date_to_text(today + datetime.timedelta(days=15)))
        elif due_level == "SOON":
            where.append("end_date>? AND end_date<=?")
            params.append(date_to_text(today + datetime.timedelta(days=15)))
            params.append(date_to_text(today + datetime.timedelta(days=30)))
        else:
            where.append("end_date>?")
            params.append(date_to_text(today + datetime.timedelta(days=30)))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    if page_no < 1:
        page_no = 1
    offset = (page_no - 1) * CONTRACT_PAGE_SIZE

    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_contract_info" + clause,
            params).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM md_contract_info" + clause +
            " ORDER BY end_date ASC, contract_id DESC LIMIT ? OFFSET ?",
            params + [CONTRACT_PAGE_SIZE, offset]).fetchall()
    finally:
        conn.close()

    records = []
    for item in rows:
        row = dict(item)
        row["due_level"] = judge_due_level(row["due_days"])
        row["due_label"] = CONTRACT_DUE_DICT[row["due_level"]]
        row["type_label"] = CONTRACT_TYPE_DICT.get(row["contract_type"],
                                                   row["contract_type"])
        row["state_label"] = CONTRACT_STATE_DICT.get(row["contract_state"],
                                                     row["contract_state"])
        row["pay_label"] = PAY_METHOD_DICT.get(row["pay_method"], row["pay_method"])
        row["settle_rate"] = round(row["settle_amount"] * 100.0 / row["sign_amount"], 1) \
            if row["sign_amount"] else 0.0
        records.append(row)
    return {
        "records": records,
        "total": total,
        "page": page_no,
        "page_size": CONTRACT_PAGE_SIZE,
        "page_amount": max(1, (total + CONTRACT_PAGE_SIZE - 1) // CONTRACT_PAGE_SIZE),
    }


def fetch_contract_stages(contract_code="", limit=60):
    """按合同读取付款节点执行明细，默认返回最近 60 条。"""
    sql = "SELECT * FROM md_contract_stage"
    params = []
    if contract_code:
        sql += " WHERE contract_code=?"
        params.append(contract_code)
    sql += " ORDER BY plan_date ASC, stage_id ASC LIMIT ?"
    params.append(limit)
    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    result = []
    for item in rows:
        row = dict(item)
        row["state_label"] = STAGE_STATE_DICT.get(row["stage_state"], row["stage_state"])
        result.append(row)
    return result


def build_vendor_summary():
    """汇总供应商与合同核心指标：档案量、合同量、金额、到期提醒与结算率。"""
    conn = get_connection()
    try:
        vendor_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_vendor_profile").fetchone()["c"]
        vendor_run = conn.execute(
            "SELECT COUNT(1) AS c FROM md_vendor_profile WHERE vendor_state='RUN'"
        ).fetchone()["c"]
        black_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM md_vendor_profile WHERE vendor_state='BLACK'"
        ).fetchone()["c"]
        contract_total = conn.execute(
            "SELECT COUNT(1) AS c FROM md_contract_info").fetchone()["c"]
        running = conn.execute(
            "SELECT COUNT(1) AS c FROM md_contract_info WHERE contract_state='RUN'"
        ).fetchone()["c"]
        expire_amount = conn.execute(
            "SELECT COUNT(1) AS c FROM md_contract_info WHERE contract_state='EXPIRE'"
        ).fetchone()["c"]
        sign_row = conn.execute(
            "SELECT COALESCE(SUM(sign_amount),0) AS s, "
            "COALESCE(SUM(settle_amount),0) AS t FROM md_contract_info "
            "WHERE contract_state<>'VOID'").fetchone()
        stage_overdue = conn.execute(
            "SELECT COUNT(1) AS c FROM md_contract_stage WHERE stage_state='OVERDUE'"
        ).fetchone()["c"]
        grade_row = conn.execute(
            "SELECT grade_code, COUNT(1) AS c FROM md_vendor_profile "
            "GROUP BY grade_code").fetchall()
    finally:
        conn.close()

    today = today_date()
    month_tail = today + datetime.timedelta(days=30)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT end_date, contract_state FROM md_contract_info").fetchall()
    finally:
        conn.close()
    soon_amount = 0
    for item in rows:
        if item["contract_state"] not in ("DRAFT", "RUN", "EXPIRE"):
            continue
        finish_day = parse_date_text(item["end_date"])
        if finish_day and today <= finish_day <= month_tail:
            soon_amount += 1

    sign_total = round(sign_row["s"], 2)
    settle_total = round(sign_row["t"], 2)
    grade_counter = {item["grade_code"]: item["c"] for item in grade_row}
    return {
        "vendor_total": vendor_total,
        "vendor_run": vendor_run,
        "black_amount": black_amount,
        "contract_total": contract_total,
        "running_amount": running,
        "expire_amount": expire_amount,
        "soon_amount": soon_amount,
        "sign_total": sign_total,
        "settle_total": settle_total,
        "settle_rate": round(settle_total * 100.0 / sign_total, 1) if sign_total else 0.0,
        "stage_overdue": stage_overdue,
        "grade_counter": grade_counter,
        "top_grade_amount": grade_counter.get("A", 0),
    }


def build_vendor_charts():
    """构建五类图表数据：等级分布、类型分布、状态分布、供应商金额排行与月度趋势。"""
    conn = get_connection()
    try:
        vendor_rows = conn.execute(
            "SELECT grade_code, vendor_type, vendor_name, contract_amount "
            "FROM md_vendor_profile").fetchall()
        contract_rows = conn.execute(
            "SELECT contract_type, contract_state, sign_amount, sign_date "
            "FROM md_contract_info").fetchall()
    finally:
        conn.close()

    grade_counter = {}
    for code in VENDOR_GRADE_DICT:
        grade_counter[code] = 0
    for item in vendor_rows:
        grade_counter[item["grade_code"]] = grade_counter.get(item["grade_code"], 0) + 1
    grade_distribution = [
        {"name": VENDOR_GRADE_DICT[code], "value": grade_counter[code]}
        for code in ("A", "B", "C", "D")
    ]

    type_counter = {}
    for item in contract_rows:
        type_counter[item["contract_type"]] = type_counter.get(item["contract_type"], 0) + 1
    type_distribution = [
        {"name": CONTRACT_TYPE_DICT.get(code, code), "value": amount}
        for code, amount in type_counter.items()
    ]

    state_counter = {}
    for item in contract_rows:
        state_counter[item["contract_state"]] = state_counter.get(item["contract_state"], 0) + 1
    state_distribution = [
        {"name": CONTRACT_STATE_DICT.get(code, code), "value": amount}
        for code, amount in state_counter.items()
    ]

    vendor_rank = sorted(
        [{"name": item["vendor_name"], "value": round(item["contract_amount"], 2)}
         for item in vendor_rows],
        key=lambda x: x["value"], reverse=True)[:6]

    month_keys = []
    cursor = today_date().replace(day=1)
    for _ in range(6):
        month_keys.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - datetime.timedelta(days=1)).replace(day=1)
    month_keys.reverse()
    month_counter = {key: 0.0 for key in month_keys}
    for item in contract_rows:
        day = parse_date_text(item["sign_date"])
        if day is None:
            continue
        key = day.strftime("%Y-%m")
        if key in month_counter:
            month_counter[key] += item["sign_amount"] or 0

    return {
        "grade_distribution": grade_distribution,
        "type_distribution": type_distribution,
        "state_distribution": state_distribution,
        "vendor_rank": vendor_rank,
        "month_trend": {
            "months": month_keys,
            "values": [round(month_counter[key], 2) for key in month_keys],
        },
    }


def fetch_vendor_options():
    """供应商合同页下拉选项：各类字典项、供应商候选与合同候选。"""
    conn = get_connection()
    try:
        vendor_rows = conn.execute(
            "SELECT vendor_code, vendor_name, vendor_state, grade_code "
            "FROM md_vendor_profile ORDER BY vendor_id").fetchall()
        contract_rows = conn.execute(
            "SELECT contract_code, contract_name FROM md_contract_info "
            "WHERE contract_state IN ('DRAFT','RUN','EXPIRE') "
            "ORDER BY contract_id DESC").fetchall()
    finally:
        conn.close()
    vendor_list = []
    for item in vendor_rows:
        row = dict(item)
        row["state_label"] = VENDOR_STATE_DICT.get(row["vendor_state"], row["vendor_state"])
        vendor_list.append(row)
    return {
        "vendor_list": vendor_list,
        "contract_list": [dict(item) for item in contract_rows],
        "type_options": [{"code": k, "label": v} for k, v in VENDOR_TYPE_DICT.items()],
        "grade_options": [{"code": k, "label": v} for k, v in VENDOR_GRADE_DICT.items()],
        "vendor_state_options": [{"code": k, "label": v}
                                 for k, v in VENDOR_STATE_DICT.items()],
        "contract_type_options": [{"code": k, "label": v}
                                  for k, v in CONTRACT_TYPE_DICT.items()],
        "contract_state_options": [{"code": k, "label": v}
                                   for k, v in CONTRACT_STATE_DICT.items()],
        "pay_options": [{"code": k, "label": v} for k, v in PAY_METHOD_DICT.items()],
        "due_options": [{"code": k, "label": v} for k, v in CONTRACT_DUE_DICT.items()],
        "stage_state_options": [{"code": k, "label": v}
                                for k, v in STAGE_STATE_DICT.items()],
    }


def build_vendor_payload(keyword="", vendor_type="", grade_code="",
                         vendor_state="", contract_keyword="", vendor_code="",
                         contract_type="", contract_state="", due_level="",
                         vendor_page=1, contract_page=1):
    """组装供应商与合同管理页面所需的全部数据。"""
    scan_contract_expire()
    vendor_pager = fetch_vendors(keyword, vendor_type, grade_code,
                                 vendor_state, vendor_page)
    contract_pager = fetch_contracts(contract_keyword, vendor_code, contract_type,
                                     contract_state, due_level, contract_page)
    return {
        "summary": build_vendor_summary(),
        "charts": build_vendor_charts(),
        "vendors": vendor_pager["records"],
        "vendor_total": vendor_pager["total"],
        "vendor_page": vendor_pager["page"],
        "vendor_page_amount": vendor_pager["page_amount"],
        "contracts": contract_pager["records"],
        "contract_total": contract_pager["total"],
        "contract_page": contract_pager["page"],
        "contract_page_amount": contract_pager["page_amount"],
        "stages": fetch_contract_stages(limit=15),
        "options": fetch_vendor_options(),
        "result": "OK",
    }


def build_vendor_brief():
    """供应商与合同看板摘要，供运维总览看板嵌入。"""
    summary = build_vendor_summary()
    return {
        "vendor_total": summary["vendor_total"],
        "contract_total": summary["contract_total"],
        "soon_amount": summary["soon_amount"],
        "expire_amount": summary["expire_amount"],
        "settle_rate": summary["settle_rate"],
        "stage_overdue": summary["stage_overdue"],
    }


# ------------------------------------------------------------------
# 演示数据
# ------------------------------------------------------------------
def seed_demo_vendor():
    """灌入供应商与合同演示数据：建档、签合同、执行部分付款节点并评级。

    :return: 生成的合同份数
    """
    conn = get_connection()
    try:
        already = conn.execute(
            "SELECT COUNT(1) AS c FROM md_vendor_profile").fetchone()["c"]
        if already:
            return 0
    finally:
        conn.close()

    today = today_date()
    vendor_codes = []
    for index, (name, vendor_type, scope) in enumerate(DEMO_VENDOR_LIST):
        outcome = save_vendor(
            build_vendor_code(index + 1), name, vendor_type,
            "苏械注准-%d-%04d" % (today.year, 1000 + index),
            "业务经理-%d" % (index + 1), "1380000%04d" % (1000 + index),
            scope, date_to_text(today - datetime.timedelta(days=365 * (index + 1))),
            "RUN", "江苏省南京市第 %d 号院区" % (index + 1),
            "演示供应商档案，含采购与维保合作记录", "医工科-周工")
        if outcome.get("success"):
            vendor_codes.append(outcome["vendor_code"])

    # 演示合同计划：（类型、付款方式、金额、履约天数、已履约天数、保留未执行的
    # 尾部节点数）。保留的尾部节点计划日期已过时即形成逾期节点，用于演示
    # 合同到期预警与供应商履约评分扣减
    contract_plan = (
        ("PURCHASE", "STAGE", 1280000, 200, 185, 2),
        ("PURCHASE", "STAGE", 860000, 160, 150, 2),
        ("MAINTAIN", "PERIOD", 420000, 365, 110, 0),
        ("MAINTAIN", "ONCE", 260000, 365, 300, 0),
        ("SERVICE", "PERIOD", 96000, 180, 200, 2),
        ("LEASE", "PERIOD", 180000, 240, 60, 0),
        ("SERVICE", "STAGE", 320000, 365, 10, 0),
        ("PURCHASE", "STAGE", 1560000, 730, 400, 2),
    )
    # 各合同承接供应商在演示供应商列表中的下标，使评级分布覆盖多个等级
    supplier_assign = (0, 1, 2, 3, 4, 0, 1, 4)
    amount = 0
    for index, (contract_type, pay_method, sign_amount, span_days, passed_days,
                keep_tail) in enumerate(contract_plan):
        vendor_index = supplier_assign[index % len(supplier_assign)]
        vendor_code = vendor_codes[vendor_index] if vendor_codes else ""
        begin_day = today - datetime.timedelta(days=passed_days)
        outcome = save_contract(
            "", "%s合同-%d 期" % (CONTRACT_TYPE_DICT[contract_type][:4], index + 1),
            vendor_code, contract_type, sign_amount,
            date_to_text(begin_day - datetime.timedelta(days=10)),
            date_to_text(begin_day),
            date_to_text(begin_day + datetime.timedelta(days=span_days)),
            12 if contract_type == "PURCHASE" else 6,
            pay_method, "DA-%d-%03d" % (today.year, index + 1), "RUN",
            "医工科-周工")
        if not outcome.get("success"):
            continue
        amount += 1
        contract_code = outcome["contract_code"]
        stages = fetch_contract_stages(contract_code)
        tail_start = len(stages) - keep_tail
        for step, stage in enumerate(stages):
            plan_day = parse_date_text(stage["plan_date"])
            if plan_day is None or plan_day > today:
                continue
            # 尾部节点刻意留作逾期样本，展示付款节点超期未执行的风险
            if step >= tail_start:
                continue
            pay_day = plan_day
            if index % 3 == 1:
                pay_day = plan_day + datetime.timedelta(days=6)
            if pay_day > today:
                continue
            execute_contract_stage(
                stage["stage_code"], stage["plan_amount"], date_to_text(pay_day),
                "演示付款执行记录", "财务科-王工")
    evaluate_vendor_grade(operator="医工科-周工")
    return amount

# --------------------------------------------------------------------------
# 第 9 天数据表脚本增量：mdm_schema.sql（M，已内联为字符串常量 DAY9_SCHEMA_SQL）
# --------------------------------------------------------------------------

DAY9_SCHEMA_SQL = r"""
-- ==================================================================
-- 第 9 天新增：供应商档案表
-- ==================================================================
-- 供应商档案表：按「一家供应商一条」维护设备厂商、代理商与第三方服务商的
-- 基础档案，含资质证照、联系人、供应品类、合作起始日期与合作状态；履约
-- 评分与评级由合同付款节点的执行情况自动回算，为采购与维保外包的供应商
-- 择优提供量化依据
CREATE TABLE IF NOT EXISTS md_vendor_profile (
    vendor_id       INTEGER PRIMARY KEY AUTOINCREMENT, -- 供应商主键
    vendor_code     TEXT    NOT NULL UNIQUE,           -- 供应商编码（VEN-序号）
    vendor_name     TEXT    NOT NULL,                  -- 供应商名称
    vendor_type     TEXT    DEFAULT 'MAKER',           -- 供应商类型（见 VENDOR_TYPE_DICT）
    license_no      TEXT    DEFAULT '',                -- 经营/医疗器械许可证号
    contact_name    TEXT    DEFAULT '',                -- 联系人姓名
    contact_phone   TEXT    DEFAULT '',                -- 联系电话
    supply_scope    TEXT    DEFAULT '',                -- 供应品类范围
    grade_code      TEXT    DEFAULT 'B',               -- 履约评级（见 VENDOR_GRADE_DICT）
    score_value     REAL    DEFAULT 0,                 -- 履约评分（0-100，由评级重算刷新）
    contract_count  INTEGER DEFAULT 0,                 -- 累计合同份数
    contract_amount REAL    DEFAULT 0,                 -- 累计合同金额（元）
    cooperate_since TEXT    DEFAULT '',                -- 合作起始日期
    vendor_state    TEXT    DEFAULT 'RUN',             -- 合作状态（见 VENDOR_STATE_DICT）
    address_text    TEXT    DEFAULT '',                -- 注册地址
    remark_text     TEXT    DEFAULT '',                -- 备注说明
    created_at      TEXT    DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 9 天新增：设备采购与维保合同表
-- ==================================================================
-- 合同信息表：按「一份合同一条」登记采购、维保、租赁、校准与服务五类合同，
-- 含签约金额、已结算金额、履约起止日期、付款方式、质保月数与合同状态；
-- 剩余天数由到期扫描刷新，形成「签约 → 分期执行 → 到期复评」的合同闭环
CREATE TABLE IF NOT EXISTS md_contract_info (
    contract_id     INTEGER PRIMARY KEY AUTOINCREMENT, -- 合同主键
    contract_code   TEXT    NOT NULL UNIQUE,           -- 合同编号（CT-年份-序号）
    contract_name   TEXT    DEFAULT '',                -- 合同名称
    vendor_code     TEXT    DEFAULT '',                -- 供应商编码
    vendor_name     TEXT    DEFAULT '',                -- 供应商名称快照
    contract_type   TEXT    DEFAULT 'PURCHASE',        -- 合同类型（见 CONTRACT_TYPE_DICT）
    sign_amount     REAL    DEFAULT 0,                 -- 签约金额（元）
    settle_amount   REAL    DEFAULT 0,                 -- 已结算金额（元）
    sign_date       TEXT    DEFAULT '',                -- 签订日期
    start_date      TEXT    DEFAULT '',                -- 履约开始日期
    end_date        TEXT    DEFAULT '',                -- 履约结束日期
    warranty_months INTEGER DEFAULT 12,                -- 质保月数
    pay_method      TEXT    DEFAULT 'STAGE',           -- 付款方式（见 PAY_METHOD_DICT）
    file_no         TEXT    DEFAULT '',                -- 归档卷宗号
    contract_state  TEXT    DEFAULT 'RUN',             -- 合同状态（见 CONTRACT_STATE_DICT）
    due_days        INTEGER DEFAULT 0,                 -- 距到期剩余天数（负数表示已逾期）
    operator_name   TEXT    DEFAULT 'admin',           -- 合同登记人
    created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- ==================================================================
-- 第 9 天新增：合同付款节点表
-- ==================================================================
-- 付款节点表：按「一份合同一个付款节点一条」拆分合同款项执行计划，区分
-- 预付款、到货验收款、质保金与尾款，记录计划金额日期与实际执行金额日期，
-- 节点执行后回写合同已结算金额，逾期节点参与供应商履约评分扣减
CREATE TABLE IF NOT EXISTS md_contract_stage (
    stage_id      INTEGER PRIMARY KEY AUTOINCREMENT,   -- 节点主键
    stage_code    TEXT    NOT NULL UNIQUE,             -- 节点编号（CS-合同序号-节点序号）
    contract_code TEXT    NOT NULL,                    -- 所属合同编号
    stage_name    TEXT    DEFAULT '',                  -- 节点名称（预付款/到货验收款等）
    plan_amount   REAL    DEFAULT 0,                   -- 计划付款金额（元）
    plan_date     TEXT    DEFAULT '',                  -- 计划付款日期
    actual_amount REAL    DEFAULT 0,                   -- 实际付款金额（元）
    actual_date   TEXT    DEFAULT '',                  -- 实际付款日期
    stage_state   TEXT    DEFAULT 'WAIT',              -- 节点状态（见 STAGE_STATE_DICT）
    delay_days    INTEGER DEFAULT 0,                   -- 执行延期天数（负数表示提前）
    remark_text   TEXT    DEFAULT '',                  -- 节点备注
    operator_name TEXT    DEFAULT 'admin',             -- 节点登记人
    created_at    TEXT    DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_vendor_code      ON md_vendor_profile (vendor_code);
CREATE INDEX IF NOT EXISTS idx_vendor_type      ON md_vendor_profile (vendor_type);
CREATE INDEX IF NOT EXISTS idx_vendor_grade     ON md_vendor_profile (grade_code);
CREATE INDEX IF NOT EXISTS idx_vendor_state     ON md_vendor_profile (vendor_state);
CREATE INDEX IF NOT EXISTS idx_contract_code    ON md_contract_info (contract_code);
CREATE INDEX IF NOT EXISTS idx_contract_vendor  ON md_contract_info (vendor_code);
CREATE INDEX IF NOT EXISTS idx_contract_type    ON md_contract_info (contract_type);
CREATE INDEX IF NOT EXISTS idx_contract_state   ON md_contract_info (contract_state);
CREATE INDEX IF NOT EXISTS idx_contract_end     ON md_contract_info (end_date);
CREATE INDEX IF NOT EXISTS idx_stage_code       ON md_contract_stage (stage_code);
CREATE INDEX IF NOT EXISTS idx_stage_contract   ON md_contract_stage (contract_code);
CREATE INDEX IF NOT EXISTS idx_stage_state      ON md_contract_stage (stage_state);
"""


def apply_day9_schema(conn):
    """把第 9 天新增的数据表脚本应用到已有数据库。"""
    conn.executescript(DAY9_SCHEMA_SQL)
    conn.commit()
    return True

# --------------------------------------------------------------------------
# 第 9 天主程序新增路由与接口：app.py（仅增量片段）
# --------------------------------------------------------------------------

# 开发日期：2026-10-29（第 9 天 / 共 15 天）
#           分析、盘点维保管理、运维日志查询、设备报废审批、计量校准管理与
#           供应商合同管理九套页面路由，并提供台账增删改查、闲置识别扫描、
#           状态切换、ROI 测算与年度收支档案维护、盘点任务生成与账实核对、
#           维保登记、运维日志检索登记闭环与 CSV 导出、报废申请发起逐级审批
#           流转与处置归档、校准计划编制、校准结果登记、证书到期扫描与校准
#           档案查询，以及供应商档案登记、合同签订与付款节点拆分执行、合同
#           到期扫描与供应商履约评级重算等 RESTful 接口，供前端表格、弹窗与
#           ECharts 图表异步取数

from mdm_vendor import (
    build_vendor_payload,
    build_vendor_brief,
    save_vendor,
    fetch_vendor_row,
    save_contract,
    execute_contract_stage,
    fetch_contract_stages,
    scan_contract_expire,
    evaluate_vendor_grade,
    seed_demo_vendor,
    VENDOR_TYPE_DICT,
    VENDOR_GRADE_DICT,
    CONTRACT_TYPE_DICT,
    CONTRACT_STATE_DICT,
)

# 【函数内插入】def api_overview():
#    # 第 9 天新增：总览叠加供应商与合同摘要，供供应商概况卡渲染
#    payload["vendor_brief"] = build_vendor_brief()

# ==================================================================
# 以下为第 9 天新增：供应商档案与合同管理接口
# ==================================================================


@app.route("/md/vendor")
def panel_vendor():
    """供应商与合同管理页面：合同概览、五类图表、档案合同节点三表联动。"""
    write_ops_journal("供应商合同", "查询", "PANEL-VENDOR", "打开供应商与合同管理页面")
    return render_template(
        "panel_vendor.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


@app.route("/api/md/vendor/panel")
def api_vendor_panel():
    """供应商合同数据接口：汇总指标、五类图表、双分页明细与下拉选项。"""
    return jsonify(build_vendor_payload(
        keyword=(request.args.get("keyword") or "").strip(),
        vendor_type=(request.args.get("vendor_type") or "").strip(),
        grade_code=(request.args.get("grade_code") or "").strip(),
        vendor_state=(request.args.get("vendor_state") or "").strip(),
        contract_keyword=(request.args.get("contract_keyword") or "").strip(),
        vendor_code=(request.args.get("vendor_code") or "").strip(),
        contract_type=(request.args.get("contract_type") or "").strip(),
        contract_state=(request.args.get("contract_state") or "").strip(),
        due_level=(request.args.get("due_level") or "").strip(),
        vendor_page=int(request.args.get("vendor_page", 1)),
        contract_page=int(request.args.get("contract_page", 1)),
    ))


@app.route("/api/md/vendor/save", methods=["POST"])
def api_vendor_save():
    """供应商档案登记接口：传入编码则更新，留空编码则按序号建档。"""
    body = request.get_json(silent=True) or {}
    result = save_vendor(
        (body.get("vendor_code") or "").strip(),
        (body.get("vendor_name") or "").strip(),
        (body.get("vendor_type") or "MAKER").strip(),
        body.get("license_no") or "",
        body.get("contact_name") or "",
        body.get("contact_phone") or "",
        body.get("supply_scope") or "",
        body.get("cooperate_since") or "",
        (body.get("vendor_state") or "RUN").strip(),
        body.get("address_text") or "",
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/vendor/detail")
def api_vendor_detail():
    """供应商档案详情接口，供编辑弹窗回填表单。"""
    vendor_row = fetch_vendor_row((request.args.get("vendor_code") or "").strip())
    return jsonify({
        "result": "OK" if vendor_row else "FAIL",
        "vendor": vendor_row,
    })


@app.route("/api/md/contract/save", methods=["POST"])
def api_contract_save():
    """合同签订接口：登记合同并按付款方式自动拆分付款节点。"""
    body = request.get_json(silent=True) or {}
    result = save_contract(
        (body.get("contract_code") or "").strip(),
        (body.get("contract_name") or "").strip(),
        (body.get("vendor_code") or "").strip(),
        (body.get("contract_type") or "PURCHASE").strip(),
        body.get("sign_amount") or 0,
        (body.get("sign_date") or "").strip(),
        (body.get("start_date") or "").strip(),
        (body.get("end_date") or "").strip(),
        body.get("warranty_months", 12),
        (body.get("pay_method") or "STAGE").strip(),
        body.get("file_no") or "",
        (body.get("contract_state") or "RUN").strip(),
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/contract/stage", methods=["POST"])
def api_contract_stage():
    """付款节点执行接口：登记实际付款并回写合同已结算金额。"""
    body = request.get_json(silent=True) or {}
    result = execute_contract_stage(
        (body.get("stage_code") or "").strip(),
        body.get("actual_amount"),
        (body.get("actual_date") or "").strip(),
        body.get("remark_text") or "",
        (body.get("operator") or "admin").strip(),
    )
    result["result"] = "OK" if result.get("success") else "FAIL"
    return jsonify(result)


@app.route("/api/md/vendor/stages")
def api_contract_stages():
    """付款节点查询接口：按合同编号返回节点执行明细。"""
    return jsonify({
        "result": "OK",
        "records": fetch_contract_stages(
            (request.args.get("contract_code") or "").strip(),
            int(request.args.get("limit", 60))),
    })


@app.route("/api/md/vendor/scan", methods=["POST"])
def api_contract_scan():
    """合同到期扫描接口：刷新剩余天数并把到期合同与逾期节点标记出来。"""
    return jsonify({
        "result": "OK",
        "amount": scan_contract_expire(),
    })


@app.route("/api/md/vendor/evaluate", methods=["POST"])
def api_vendor_evaluate():
    """供应商履约评级接口：按付款与合同执行情况重算评分与等级。"""
    body = request.get_json(silent=True) or {}
    amount = evaluate_vendor_grade((body.get("operator") or "admin").strip())
    return jsonify({
        "result": "OK",
        "success": True,
        "amount": amount,
        "message": "履约评级重算完成，覆盖 %d 家供应商" % amount,
    })


@app.route("/api/md/vendor/options")
def api_vendor_options():
    """供应商合同下拉选项接口：类型、评级、状态、付款方式与到期等级字典。"""
    return jsonify({
        "result": "OK",
        "type_options": [{"code": k, "label": v} for k, v in VENDOR_TYPE_DICT.items()],
        "grade_options": [{"code": k, "label": v} for k, v in VENDOR_GRADE_DICT.items()],
        "contract_type_options": [{"code": k, "label": v}
                                  for k, v in CONTRACT_TYPE_DICT.items()],
        "contract_state_options": [{"code": k, "label": v}
                                   for k, v in CONTRACT_STATE_DICT.items()],
    })



# 【函数内插入】def bootstrap_application():
#    seed_demo_vendor()

# 【函数内插入】if __name__ == "__main__":
#    print("供应商合同： http://127.0.0.1:%d/md/vendor" % PORT)

# --------------------------------------------------------------------------
# 第 9 天新增/修改的前端资源清单（文件本体见工程 templates/、static/ 目录）
# --------------------------------------------------------------------------

#   [新增] static/mdm_vendor.js             新增 559 行
#   [修改] templates/layout_mdm.html        新增 1 行
#   [新增] templates/panel_vendor.html      新增 415 行
