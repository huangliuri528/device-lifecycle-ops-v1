# 软件全称：医疗器械全生命周期运维管理平台 V1.0
# 版本：V1.0
# 开发日期：2026-10-21（第 1 天 / 共 15 天）
# 模块功能：Flask 主程序入口。完成应用工厂创建、数据库初始化、演示数据
#           灌入、蓝图路由注册（运维总览看板 / 台账占位 / 统计接口），
#           并提供 JSON 接口供前端 ECharts 异步取数
# 依赖包：Flask==3.0.3
# 启动命令：python app.py  （默认 http://127.0.0.1:5001/md/overview）

from flask import Flask, render_template, jsonify
from mdm_settings import HOST, PORT, DEBUG, SOFTWARE_FULL_NAME, SOFTWARE_VERSION
from mdm_dal import init_database, seed_demo_assets, build_overview_payload, write_ops_journal

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


@app.before_request
def before_first_request_hook():
    """占位钩子：后续第 8 天接入登录鉴权时在此处做会话校验。"""
    return None


@app.route("/")
def index_redirect():
    """根路径跳转到运维总览看板。"""
    from flask import redirect, url_for
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
    """设备台账页面占位路由，第 2 天实现完整 CRUD 页面。"""
    write_ops_journal("设备台账", "查询", "PANEL-ASSETS", "打开设备台账页面（首日占位）")
    return render_template(
        "panel_assets.html",
        software_name=SOFTWARE_FULL_NAME,
        software_version=SOFTWARE_VERSION,
    )


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
    print("访问地址： http://127.0.0.1:%d/md/overview" % PORT)
    print("=" * 62)
    app.run(host=HOST, port=PORT, debug=DEBUG)
