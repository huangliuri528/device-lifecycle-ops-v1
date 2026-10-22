# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-22（第 2 天 / 共 15 天）
# 模块功能：Flask 主程序入口。完成应用创建、数据库初始化、演示数据灌入，
#           注册运维总览看板路由与设备台账增删改查（CRUD）页面及配套
#           RESTful 接口，供前端表格与弹窗异步调用
# 依赖包：Flask==3.0.3
# 启动命令：python app.py  （默认 http://127.0.0.1:5001/md/overview）

from flask import Flask, render_template, jsonify, redirect, url_for, request
from mdm_settings import HOST, PORT, DEBUG, SOFTWARE_FULL_NAME, SOFTWARE_VERSION
from mdm_dal import (
    init_database,
    seed_demo_assets,
    build_overview_payload,
    write_ops_journal,
    fetch_asset_by_id,
    fetch_status_flow,
    fetch_latest_journals,
)
from mdm_ledger import (
    create_asset_record,
    modify_asset_record,
    remove_asset_record,
    search_asset_page,
    build_ledger_options,
    summarize_ledger_value,
)

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


@app.route("/api/md/overview")
def api_overview():
    """看板数据接口：返回设备总数、状态分布、分类分布与近期台账清单。"""
    payload = build_overview_payload()
    payload["result"] = "OK"
    return jsonify(payload)


@app.route("/api/md/health")
def api_health():
    """系统健康检查接口，用于部署后快速验证服务是否可用。"""
    return jsonify({
        "result": "OK",
        "software": SOFTWARE_FULL_NAME,
        "version": SOFTWARE_VERSION,
    })


def bootstrap_application():
    """应用启动引导：建表 + 灌入演示数据，保证首日启动即有看板效果。"""
    init_database()
    seed_demo_assets()


if __name__ == "__main__":
    bootstrap_application()
    print("=" * 62)
    print("%s %s 启动中..." % (SOFTWARE_FULL_NAME, SOFTWARE_VERSION))
    print("运维总览： http://127.0.0.1:%d/md/overview" % PORT)
    print("设备台账： http://127.0.0.1:%d/md/assets" % PORT)
    print("=" * 62)
    app.run(host=HOST, port=PORT, debug=DEBUG)
